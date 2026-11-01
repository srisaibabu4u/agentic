"""Master orchestrator.

The master never does work itself. On every event it reads the case record,
checks guardrails, applies the first routing rule that matches and returns a
Decision with zero or more tasks for the specialised agents.

Routing is deterministic code on purpose: the same state always gives the same
route, and compliance can read the rules. In --live mode an LLM writes the
plain-English rationale for the audit log (prompts/master.md), and can run in
"shadow" mode to compare its own routing with the rules.
"""
from __future__ import annotations

from datetime import datetime

from .models import A1, A2, A3, A4, A5, STATUS_ON_DISPATCH, Case, Decision, Event, Task

MAX_RETRIES = 3
LOW_CONFIDENCE = 0.80


def retry_key(agent: str, scope: dict) -> str:
    """G4 counts failures of one agent on one piece of work. Each retry is a new task with a new id,
    so the key is the agent and the scope it was given, not the task id."""
    return f"{agent}:{sorted((k, str(v)) for k, v in (scope or {}).items())}"


def business_days_between(start: datetime, end_date: str) -> int:
    from datetime import date, timedelta
    d, end = start.date(), date.fromisoformat(end_date)
    n = 0
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


class Master:
    def __init__(self, config: dict):
        self.cfg = config
        self.dep = config["dependency_map"]
        self.all_questions = [q["q_id"] for q in config["questionnaire"]["questions"]]

    # ------------------------------------------------------------------ helpers
    def _task(self, case: Case, agent: str, scope: dict, inputs: dict | None = None,
              depends_on: list | None = None) -> Task:
        return Task(case.next_task_id(), agent, scope, inputs or {}, depends_on or [])

    def _notify(self, case: Case, now: datetime, template: str, to: list[str], items: list[str],
                context: dict | None = None, guards: list | None = None) -> Task:
        inputs = {"template": template, "to": to, "items": items, "context": context or {}}
        # G6: close to due date -> copy the team lead on every notification
        if business_days_between(now, case.meta["due_date"]) < 5:
            inputs["escalate_cc"] = case.meta["team_lead"]["id"]
            if guards is not None:
                guards.append("G6")
        return self._task(case, A5, {"template": template}, inputs)

    def questions_for_fields(self, field_ids) -> list[str]:
        qs = set()
        for f in field_ids:
            qs.update(self.dep["field_to_questions"].get(f, []))
        return sorted(qs)

    def questions_for_docs(self, docs: list[dict]) -> list[str]:
        fields = []
        for d in docs:
            fields += self.dep["doc_item_to_fields"].get(d["item"], [])
        return self.questions_for_fields(fields)

    @staticmethod
    def _status_for(tasks: list[Task], default: str) -> str:
        for t in tasks:  # first non-notification task decides the status
            if t.agent in STATUS_ON_DISPATCH:
                return STATUS_ON_DISPATCH[t.agent]
        return default

    # ------------------------------------------------------------------ decide
    def decide(self, case: Case, ev: Event, now: datetime) -> Decision:
        g: list[str] = []
        M = case.meta

        # Anything still in flight (parallel or chained tasks)? Then just wait.
        if case.pending and ev.type == "AGENT_RESULT":
            return Decision("WAIT", case.status, [], ["WAIT_PENDING"], g,
                            f"{len(case.pending)} dispatched task(s) still running; "
                            "re-evaluate when they return.")

        # ---- R1: new case
        if ev.type == "RENEWAL_DUE":
            t = self._task(case, A1, {"checklist": self.cfg["checklist"]["id"]},
                           {"client_id": M["client_id"], "uen": M["uen"]})
            return Decision("DISPATCH", "DISCOVERY", [t], ["R1"], g,
                            "New renewal case with no evidence yet, so start with document discovery.")

        # ---- agent results
        if ev.type == "AGENT_RESULT":
            agent, res = ev.source, ev.payload

            if res.get("status") == "FAILED":
                key = retry_key(agent, res.get("scope", {}))
                n = case.retries.get(key, 0) + 1
                case.retries[key] = n
                if n >= MAX_RETRIES:
                    g.append("G4")
                    t = self._notify(case, now, "ESCALATION", [M["maker"]["id"]],
                                     [f"{agent} failed {n} times: {res.get('error', 'unknown error')}"], guards=g)
                    return Decision("ESCALATE", "WITH_MAKER", [t], ["R3a"], g,
                                    f"{agent} failed {n} times on the same item; stop retrying and hand to the maker.")
                t = self._task(case, agent, res.get("scope", {}), res.get("inputs", {}))
                t.retry = n
                return Decision("DISPATCH", case.status, [t], ["R3a"], g, f"{agent} failed; retry {n} of {MAX_RETRIES - 1}.")

            if agent == A5:
                return Decision("WAIT", case.status, [], ["A5_ACK"], g,
                                "Notification delivered. No state change; waiting on the recipient.")

            if agent == A1:
                missing = res.get("missing", [])
                found = [d["doc_id"] for d in res.get("found", [])]
                if not found:
                    t = self._notify(case, now, "ESCALATION", [M["maker"]["id"]], ["No documents found for client"], guards=g)
                    return Decision("ESCALATE", "WITH_MAKER", [t], ["R2a"], g, "Discovery found nothing; the maker must check the client record.")
                if any(m["identity_critical"] for m in missing):
                    t = self._notify(case, now, "DOC_REQUEST", [M["maker"]["id"]],
                                     [m["label"] for m in missing], guards=g)
                    return Decision("DISPATCH", "WITH_MAKER", [t], ["R2a"], g,
                                    "An identity document is missing or stale; drafting would rest on unverified identity, so ask the maker first.")
                if len(missing) <= 2:
                    tasks = [self._task(case, A2, {"docs": found})]
                    if missing:
                        tasks.append(self._notify(case, now, "DOC_REQUEST", [M["maker"]["id"]],
                                                  [m["label"] for m in missing],
                                                  {"reasons": {m["label"]: m["reason"] for m in missing}}, guards=g))
                    return Decision("DISPATCH", "INGESTION", tasks, ["R2"], g,
                                    f"{len(found)} documents found; {len(missing)} non-identity item(s) missing or stale. "
                                    "Ingest now and request the rest from the maker in parallel.")
                t = self._notify(case, now, "DOC_REQUEST", [M["maker"]["id"]], [m["label"] for m in missing], guards=g)
                return Decision("DISPATCH", "WITH_MAKER", [t], ["R2a"], g, "Too many documents missing to draft usefully.")

            if agent == A2:
                if any(k.startswith("ubo") or k.startswith("turnover") for k, v in case.open_items.items() if "LOW_CONF" in v):
                    g.append("G1")
                if "screening.hits" in case.open_items:
                    g.append("G2")
                t = self._task(case, A3, {"questions": self.all_questions, "mode": "draft"},
                               {"open_items": dict(case.open_items)})
                return Decision("DISPATCH", "IN_DRAFT", [t], ["R3"], g,
                                f"{len(case.fields)} fields extracted. Draft all answers now and carry "
                                f"{len(case.open_items)} open item(s) forward as flags for the maker.")

            if agent == A3:
                if res.get("mode") == "suggest":
                    return Decision("WAIT", "RETURNED", [], ["R8"], g, "Suggested redraft prepared; waiting on the maker.")
                if case.open_items:
                    g += [x for x in ("G1", "G2") if x not in g]
                    items = [f"{ref}: {why}" for ref, why in case.open_items.items()]
                    missing = [m["label"] for m in (case.manifest or {}).get("missing", [])
                               if not any(d["item"] == m["checklist_item"] and d.get("fresh", True) for d in case.docs.values())]
                    items += [f"Upload: {m}" for m in missing]
                    t = self._notify(case, now, "MAKER_REVIEW", [M["maker"]["id"]], items, guards=g)
                    return Decision("DISPATCH", "WITH_MAKER", [t], ["R4"], g,
                                    f"Draft complete but {len(case.open_items)} item(s) need a human, so it goes to the maker before review.")
                t = self._task(case, A4, {"questions": sorted(case.answers)})
                return Decision("DISPATCH", "AUTO_REVIEW", [t], ["R4b"], g, "Draft has no open items; run the automated review.")

            if agent == A4:
                findings = res.get("findings", [])
                if res.get("verdict") == "PASS":
                    if case.maker_submitted:
                        t = self._notify(case, now, "CHECKER_REVIEW", [M["checker"]["id"]],
                                         [f"Approve proposed rating {case.rating['band']} ({case.rating['total']}/{case.rating['max']})"],
                                         {"changes": list(case.maker_inputs["edits"]) + list(case.maker_inputs["dispositions"]),
                                          "returned_before": [r["q_id"] for r in case.checker_returns]}, guards=g)
                        g.append("G5")
                        return Decision("DISPATCH", "WITH_CHECKER", [t], ["R6"], g,
                                        "Automated review passed with no findings and the maker has submitted. Only a checker can approve, so route to the checker.")
                    t = self._notify(case, now, "MAKER_REVIEW", [M["maker"]["id"]], ["Review draft and submit"], guards=g)
                    return Decision("DISPATCH", "WITH_MAKER", [t], ["R6"], g, "Review passed but the maker has not submitted yet.")
                if any(f["severity"] == "HIGH" for f in findings):
                    g.append("G3")
                    t = self._notify(case, now, "MAKER_REVIEW", [M["maker"]["id"]],
                                     [f"{f['q_id']}: {f['issue']}" for f in findings], guards=g)
                    return Decision("DISPATCH", "WITH_MAKER", [t], ["G3"], g,
                                    "A HIGH-severity finding cannot be fixed by an agent; return to the maker.")
                fixable = [f for f in findings if f["evidence_available"]]
                if len(fixable) == len(findings):
                    qs = sorted({f["q_id"] for f in findings})
                    over = [q for q in qs if case.retries.get(f"A3:{q}", 0) >= MAX_RETRIES]
                    if over:
                        g.append("G4")
                    else:
                        for q in qs:
                            case.retries[f"A3:{q}"] = case.retries.get(f"A3:{q}", 0) + 1
                        t1 = self._task(case, A3, {"questions": qs, "mode": "redraft"}, {"notes": findings})
                        t2 = self._task(case, A4, {"questions": sorted(case.answers)}, depends_on=[t1.task_id])
                        return Decision("DISPATCH", "IN_DRAFT", [t1, t2], ["R7"], g,
                                        f"{len(findings)} MEDIUM/LOW finding(s) and the facts to fix them are already on file. "
                                        "Let Agent 3 fix them, then re-run Agent 4.")
                t = self._notify(case, now, "MAKER_REVIEW", [M["maker"]["id"]],
                                 [f"{f['q_id']}: {f['issue']}" for f in findings], guards=g)
                return Decision("DISPATCH", "WITH_MAKER", [t], ["R7b"], g,
                                "Fixing the finding needs information that is not on file; ask the maker.")

        # ---- human events
        if ev.type == "MAKER_SUBMIT":
            p = ev.payload
            if "screening.hits" in case.open_items:
                g.append("G2")
                t = self._notify(case, now, "ESCALATION", [M["maker"]["id"]], ["Disposition the open screening hit before submitting"], guards=g)
                return Decision("ESCALATE", "WITH_MAKER", [t], ["G2"], g, "Maker submitted with an undispositioned screening hit.")
            new_docs = p.get("uploads", [])
            touched = set(self.questions_for_fields(list(p.get("field_edits", {})) + list(p.get("dispositions", {}))))
            touched |= {r["q_id"] for r in case.checker_returns if not r.get("resolved")}
            if new_docs:
                touched |= set(self.questions_for_docs(new_docs))
                t1 = self._task(case, A2, {"docs": [d["doc_id"] for d in new_docs]})
                t2 = self._task(case, A3, {"questions": sorted(touched), "mode": "redraft"},
                                {"notes": [{"q_id": r["q_id"], "issue": r["comment"]} for r in case.checker_returns if not r.get("resolved")]},
                                depends_on=[t1.task_id])
                return Decision("DISPATCH", "INGESTION", [t1, t2], ["R5"], g,
                                f"Maker added {len(new_docs)} document(s). Ingest only those, then redraft only the affected "
                                f"questions ({', '.join(sorted(touched))}). Nothing else is re-run.")
            if touched:
                t = self._task(case, A3, {"questions": sorted(touched), "mode": "redraft"})
                return Decision("DISPATCH", "IN_DRAFT", [t], ["R5"], g, "Maker edited values; redraft the affected answers.")
            t = self._task(case, A4, {"questions": sorted(case.answers)})
            return Decision("DISPATCH", "AUTO_REVIEW", [t], ["R5c"], g, "Maker submitted without changes; run the automated review.")

        if ev.type == "CHECKER_RETURN":
            qs = [r["q_id"] for r in ev.payload["returned"]]
            t1 = self._task(case, A3, {"questions": qs, "mode": "suggest"}, {"notes": ev.payload["returned"]})
            t2 = self._notify(case, now, "CHECKER_RETURN", [M["maker"]["id"]],
                              [f"{r['q_id']}: {r['comment']}" for r in ev.payload["returned"]], guards=g)
            return Decision("DISPATCH", "RETURNED", [t1, t2], ["R8"], g,
                            "The checker asked for facts that are not on file. Only the maker can get them, "
                            "so prepare a suggested redraft and notify the maker.")

        if ev.type == "CHECKER_APPROVE":
            g.append("G5")
            if M["maker"]["id"] == M["checker"]["id"]:
                return Decision("ESCALATE", case.status, [], ["G5"], g, "Maker and checker are the same person; approval rejected.")
            t = self._notify(case, now, "CASE_CLOSED", [M["maker"]["id"], M["checker"]["id"]],
                             [f"Final rating {ev.payload['final_rating']}", f"Next review {ev.payload['next_review']}"], guards=g)
            return Decision("CLOSE", "CLOSED", [t], ["R9"], g,
                            "Checker approved. Close the case, archive the audit pack and schedule the next review.")

        return Decision("ESCALATE", case.status, [], [], g, f"No rule matches event {ev.type}; escalate rather than guess.")
