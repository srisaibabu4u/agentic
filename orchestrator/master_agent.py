"""Agentic master: Claude decides every next step; code enforces the guardrails.

    AgenticMaster(cfg, llm).decide(case, event, now) -> Decision   (same interface as Master)

On each event the master agent reads the case with read-only tools, decides
what happens next and submits a decision. Code then:

* checks the proposal against the guardrails (check_decision). A proposal that
  breaks one is sent back to the agent as a tool error to fix; the agent
  cannot override a guardrail;
* applies what must be exact: real task ids, the G6 team-lead copy, the retry
  counters behind G4, and the list of guardrails that applied;
* records, next to the decision, what the rule playbook (Master) would have
  done, so compliance can see where the agent used its own judgement.

Two kinds of event have only one legal outcome and are decided by code without
a model call: a result while other tasks are still running (wait), and a
notification receipt (wait). If the agent gives no acceptable decision within
its turn budget, the playbook decides and the decision is marked as a fallback.
"""
from __future__ import annotations

import copy
from datetime import datetime

from .master import MAX_RETRIES, Master, business_days_between, retry_key
from .models import A1, A2, A3, A4, A5, STATUS_ON_DISPATCH, STATUSES, Case, Decision, Event, Task
from .toolkit import Tool

AGENTS = (A1, A2, A3, A4, A5)
DECISIONS = ("DISPATCH", "WAIT", "CLOSE", "ESCALATE")
HUMAN_STATUSES = ("WITH_MAKER", "WITH_CHECKER", "RETURNED")
TEMPLATES = ("DOC_REQUEST", "MAKER_REVIEW", "CHECKER_REVIEW", "CHECKER_RETURN", "CASE_CLOSED", "ESCALATION")
PLAYBOOK = {"R1", "R2", "R2a", "R3", "R3a", "R4", "R4b", "R5", "R5c", "R6", "R7", "R7b", "R8", "R9",
            "G1", "G2", "G3", "G4", "G5", "G6", "JUDGEMENT"}
MAX_TASKS = 6


# ============================================================ guardrails in code
def check_decision(cfg: dict, case: Case, ev: Event, now: datetime, p: dict) -> list[str]:
    """Every reason the proposal p may not be applied. [] means it is allowed."""
    M, errs = case.meta, []
    people = {M["maker"]["id"], M["checker"]["id"], M["team_lead"]["id"]}
    questions = [q["q_id"] for q in cfg["questionnaire"]["questions"]]
    d, status, tasks = p.get("decision"), p.get("new_status"), p.get("tasks") or []
    if d not in DECISIONS:
        return [f"decision must be one of {DECISIONS}"]
    if status not in STATUSES:
        return [f"new_status must be one of {STATUSES}"]
    if not str(p.get("rationale", "")).strip():
        errs.append("rationale is required: one or two sentences for the audit log")
    unknown = [r for r in p.get("rules_matched") or [] if r not in PLAYBOOK]
    if not p.get("rules_matched") or unknown:
        errs.append(f"rules_matched must name playbook rules, or JUDGEMENT when none fits; unknown {unknown}")
    if len(tasks) > MAX_TASKS:
        errs.append(f"at most {MAX_TASKS} tasks per decision")

    # ---- shape of the decision
    work = [t for t in tasks if t.get("agent") != A5]
    if d == "DISPATCH" and not tasks:
        errs.append("DISPATCH needs at least one task")
    if d == "WAIT" and tasks:
        errs.append("WAIT dispatches no tasks")
    if d in ("ESCALATE", "CLOSE") and work:
        errs.append(f"{d} may only send notifications, not agent work")
    failed = ev.type == "AGENT_RESULT" and ev.payload.get("status") == "FAILED"
    if work and work[0].get("agent") in STATUS_ON_DISPATCH:
        first = work[0]
        # a suggested redraft is work for the maker, so the case stays RETURNED
        want = "RETURNED" if first["agent"] == A3 and (first.get("scope") or {}).get("mode") == "suggest"             else STATUS_ON_DISPATCH[first["agent"]]
        if status != want and not (failed and status == case.status):  # a retry keeps the status it failed in
            errs.append(f"the first agent task is {first['agent']}, so new_status must be {want}")
    if not work and status not in HUMAN_STATUSES + ("CLOSED",) and status != case.status:
        errs.append(f"no agent work is dispatched, so the case must wait on a person ({', '.join(HUMAN_STATUSES)}); "
                    f"{status} would leave it stuck")
    if not work and case.pending == [] and status in ("NEW", "DISCOVERY", "INGESTION", "IN_DRAFT", "AUTO_REVIEW"):
        errs.append(f"nothing is running, so status {status} would leave the case stuck; dispatch work or hand it to a person")

    # ---- each task
    refs = []
    for i, t in enumerate(tasks):
        a, scope, inputs, ref = t.get("agent"), t.get("scope") or {}, t.get("inputs") or {}, t.get("ref") or f"t{i + 1}"
        where = f"task {ref}"
        for dep in t.get("depends_on") or []:
            if dep not in refs:
                errs.append(f"{where}: depends_on '{dep}' must be the ref of an earlier task in this decision")
        refs.append(ref)
        if a not in AGENTS:
            errs.append(f"{where}: agent must be one of {AGENTS}")
        elif a == A2:
            docs = scope.get("docs") or []
            bad = [x for x in docs if x not in case.docs]
            if not docs or bad:
                errs.append(f"{where}: A2 scope.docs must list documents on the case {sorted(case.docs)}; bad {bad}")
        elif a == A3:
            qs, mode = scope.get("questions") or [], scope.get("mode")
            if mode not in ("draft", "redraft", "suggest"):
                errs.append(f"{where}: A3 scope.mode must be draft, redraft or suggest")
            if not qs or set(qs) - set(questions):
                errs.append(f"{where}: A3 scope.questions must be a non-empty subset of {questions}")
        elif a == A4:
            qs = scope.get("questions") or []
            drafted = set(case.answers) | {q for x in tasks[:i] if x.get("agent") == A3 for q in x["scope"].get("questions", [])}
            if not qs or set(qs) - drafted:
                errs.append(f"{where}: A4 can only review drafted answers {sorted(drafted)}")
        elif a == A5:
            if inputs.get("template") not in TEMPLATES:
                errs.append(f"{where}: A5 inputs.template must be one of {TEMPLATES}")
            to = inputs.get("to") or []
            if not to or set(to) - people:
                errs.append(f"{where}: A5 inputs.to must be user ids from {sorted(people)}")
            if not inputs.get("items") or not all(isinstance(x, str) for x in inputs["items"]):
                errs.append(f"{where}: A5 inputs.items must be a non-empty list of strings")
            if inputs.get("template") == "CHECKER_REVIEW" and to != [M["checker"]["id"]]:
                errs.append(f"{where}: only the checker receives CHECKER_REVIEW")

    agents = [t.get("agent") for t in tasks]
    res = ev.payload if ev.type == "AGENT_RESULT" else {}
    to_checker = (status == "WITH_CHECKER" and case.status != "WITH_CHECKER") or any((t.get("inputs") or {}).get("template") == "CHECKER_REVIEW" for t in tasks)

    # ---- in-flight work and receipts (decided by code, but checked anyway)
    if case.pending and ev.type == "AGENT_RESULT" and d != "WAIT":
        errs.append("other tasks are still running; WAIT for them")
    if ev.type == "AGENT_RESULT" and ev.source == A5 and d != "WAIT":
        errs.append("a notification receipt changes nothing; WAIT")
    # ---- G1 / G2: nothing goes to the checker with items only a person can close
    if to_checker and case.open_items:
        errs.append(f"G1/G2: open items {sorted(case.open_items)} must be closed by the maker before the checker sees the case")
    if "screening.hits" in case.open_items:
        if A4 in agents:
            errs.append("G2: an undispositioned screening hit must be closed by the maker before review")
        if ev.type == "MAKER_SUBMIT" and (d != "ESCALATE" or status != "WITH_MAKER"):
            errs.append("G2: the maker submitted with an open screening hit; ESCALATE back to the maker")
    # ---- G3: HIGH findings go to the maker, never back to an agent
    if ev.type == "AGENT_RESULT" and ev.source == A4 and any(f.get("severity") == "HIGH" for f in res.get("findings", [])):
        if work or status != "WITH_MAKER" or not _notifies(tasks, M["maker"]["id"]):
            errs.append("G3: a HIGH finding goes to the maker (A5 to the maker, status WITH_MAKER), never to an agent")
    # ---- G4: retry limit
    if ev.type == "AGENT_RESULT" and res.get("status") == "FAILED":
        n = case.retries.get(retry_key(ev.source, res.get("scope", {})), 0) + 1
        if n >= MAX_RETRIES and (ev.source in agents or d != "ESCALATE"):
            errs.append(f"G4: {ev.source} has failed {n} times on this work; ESCALATE to the maker, do not retry")
    if ev.type == "AGENT_RESULT" and ev.source == A4:
        for t in tasks:
            if t.get("agent") == A3 and (t.get("scope") or {}).get("mode") == "redraft":
                over = [q for q in t["scope"].get("questions", []) if case.retries.get(f"A3:{q}", 0) >= MAX_RETRIES]
                if over:
                    errs.append(f"G4: {over} were redrafted {MAX_RETRIES} times already; send them to the maker")
    # ---- G5: only the checker approves; four eyes
    same = M["maker"]["id"] == M["checker"]["id"]
    if (d == "CLOSE" or status in ("APPROVED", "CLOSED")) and (ev.type != "CHECKER_APPROVE" or same):
        errs.append("G5: only a checker approval (by someone other than the maker) can close the case")
    if ev.type == "CHECKER_APPROVE" and not same and d != "CLOSE":
        errs.append("G5: the checker approved; CLOSE the case and notify maker and checker")
    if ev.type == "CHECKER_APPROVE" and same and d != "ESCALATE":
        errs.append("G5: maker and checker are the same person; ESCALATE, do not close")
    if to_checker and not (case.maker_submitted and ev.type == "AGENT_RESULT" and ev.source == A4
                           and res.get("verdict") == "PASS"):
        errs.append("G5: the case goes to the checker only after a passing review of a draft the maker has submitted")
    if ev.type == "CHECKER_RETURN" and status not in ("RETURNED", "WITH_MAKER", "IN_DRAFT"):
        errs.append("the checker returned the case; it goes back to the maker's side (RETURNED)")
    return errs


def _notifies(tasks: list[dict], user_id: str) -> bool:
    return any(t.get("agent") == A5 and user_id in (t.get("inputs") or {}).get("to", []) for t in tasks)


def apply_decision(cfg: dict, case: Case, ev: Event, now: datetime, p: dict) -> Decision:
    """Turn an allowed proposal into a Decision. Code sets everything that must be exact."""
    M, g = case.meta, set(p.get("guardrails_triggered") or [])
    ids, tasks = {}, []
    late = business_days_between(now, M["due_date"]) < 5
    for i, t in enumerate(p.get("tasks") or []):
        task = Task(case.next_task_id(), t["agent"], dict(t.get("scope") or {}), dict(t.get("inputs") or {}),
                    [ids[d] for d in t.get("depends_on") or []])
        ids[t.get("ref") or f"t{i + 1}"] = task.task_id
        if task.agent == A5:
            task.scope = {"template": task.inputs["template"]}
            task.inputs.setdefault("context", {})
            if late:  # G6
                task.inputs["escalate_cc"] = M["team_lead"]["id"]
                g.add("G6")
        tasks.append(task)

    res = ev.payload if ev.type == "AGENT_RESULT" else {}
    if ev.type == "AGENT_RESULT" and res.get("status") == "FAILED":  # G4 counter
        key = retry_key(ev.source, res.get("scope", {}))
        case.retries[key] = case.retries.get(key, 0) + 1
        for t in tasks:
            if t.agent == ev.source:
                t.retry = case.retries[key]
        if case.retries[key] >= MAX_RETRIES:
            g.add("G4")
    if ev.type == "AGENT_RESULT" and ev.source == A4:
        for t in tasks:
            if t.agent == A3 and t.scope.get("mode") == "redraft":
                for q in t.scope.get("questions", []):
                    case.retries[f"A3:{q}"] = case.retries.get(f"A3:{q}", 0) + 1
        if any(f.get("severity") == "HIGH" for f in res.get("findings", [])):
            g.add("G3")
    if "screening.hits" in case.open_items:
        g.add("G2")
    if ev.type in ("CHECKER_APPROVE",) or p.get("new_status") == "WITH_CHECKER":
        g.add("G5")
    return Decision(p["decision"], p["new_status"], tasks, list(p.get("rules_matched") or []), sorted(g),
                    p["rationale"].strip())


def to_proposal(d: Decision) -> dict:
    """A Decision in the shape the master agent submits (tests and comparisons)."""
    refs = {t.task_id: f"t{i + 1}" for i, t in enumerate(d.tasks)}
    return {"decision": d.decision, "new_status": d.new_status, "rules_matched": d.rules_matched,
            "guardrails_triggered": d.guardrails_triggered, "rationale": d.rationale,
            "tasks": [{"ref": refs[t.task_id], "agent": t.agent, "scope": t.scope,
                       "inputs": {k: v for k, v in t.inputs.items() if k != "escalate_cc"},
                       "depends_on": [refs[x] for x in t.depends_on]} for t in d.tasks]}


# ============================================================ the agent
class AgenticMaster:
    prompt_file = "master.md"

    def __init__(self, cfg: dict, llm):
        self.cfg, self.llm = cfg, llm
        self.rules = Master(cfg)
        self.reference: Decision | None = None   # what the playbook would do; for the audit, not shown to the agent

    def decide(self, case: Case, ev: Event, now: datetime) -> Decision:
        self.reference = self.rules.decide(copy.deepcopy(case), ev, now)
        if (case.pending and ev.type == "AGENT_RESULT") or (ev.type == "AGENT_RESULT" and ev.source == A5):
            d = self.rules.decide(case, ev, now)  # only one legal outcome: wait
            d.meta = {"decided_by": "code", "why": "only one allowed outcome (wait)"}
            return d

        out = self.llm.run_agent(self.prompt_file, self.brief(case, ev, now), self.tools(case, ev, now),
                                 lambda p: check_decision(self.cfg, case, ev, now, p), bank_name="Example Bank")
        trace = out.pop("agent_trace", None)
        if out.get("status") == "FAILED" and "decision" not in out:
            d = self.rules.decide(case, ev, now)
            d.meta = {"decided_by": "playbook_fallback", "error": out.get("error"), "agent_trace": trace}
            return d
        d = apply_decision(self.cfg, case, ev, now, out)
        ref = to_proposal(self.reference)
        d.meta = {"decided_by": "master_agent", "agent_trace": trace,
                  "playbook": {"decision": ref["decision"], "new_status": ref["new_status"],
                               "rules_matched": ref["rules_matched"], "agents": [t["agent"] for t in ref["tasks"]]},
                  "agrees_with_playbook": (d.decision, d.new_status, sorted(t.agent for t in d.tasks)) ==
                                          (ref["decision"], ref["new_status"], sorted(t["agent"] for t in ref["tasks"]))}
        return d

    def brief(self, case: Case, ev: Event, now: datetime) -> dict:
        return {"case_id": case.case_id, "now": now.isoformat(), "status": case.status,
                "event": {"type": ev.type, "source": ev.source, "task_id": ev.task_id},
                "business_days_to_due": business_days_between(now, case.meta["due_date"])}

    def tools(self, case: Case, ev: Event, now: datetime) -> list[Tool]:
        M, dep = case.meta, self.cfg["dependency_map"]
        missing = [m for m in (case.manifest or {}).get("missing", [])
                   if not any(d["item"] == m["checklist_item"] and d.get("fresh", True) for d in case.docs.values())]

        def record():
            return {
                "status": case.status, "due_date": M["due_date"],
                "business_days_to_due": business_days_between(now, M["due_date"]),
                "people": {r: {"id": M[r]["id"], "name": M[r]["name"]} for r in ("maker", "checker", "team_lead")},
                "documents": [{k: d.get(k) for k in ("doc_id", "item", "type", "date", "fresh")} for d in case.docs.values()],
                "missing_documents": missing,
                "fields_extracted": len(case.fields), "open_items": case.open_items,
                "answers": {q: {"score": a.get("score"), "flags": a.get("flags")} for q, a in sorted(case.answers.items())},
                "rating": case.rating, "findings": case.findings, "maker_submitted": case.maker_submitted,
                "maker_inputs": case.maker_inputs, "checker_returns": case.checker_returns,
                "retries": case.retries, "tasks_running": [f"{t.task_id}:{t.agent}" for t in case.pending],
            }

        def questions_for(field_ids: list[str] | None = None, doc_ids: list[str] | None = None):
            fields = list(field_ids or [])
            for d in doc_ids or []:
                item = (case.docs.get(d) or {}).get("item")
                fields += dep["doc_item_to_fields"].get(item, [])
            return {"fields": sorted(set(fields)), "questions": self.rules.questions_for_fields(fields)}

        return [
            Tool("get_case_record", "The whole case: documents, missing items, open items, answers, findings, "
                                    "maker and checker actions, retries and running tasks.", {}, record),
            Tool("get_event", "The event to decide on, in full (agent result or human action payload).", {},
                 lambda: {"type": ev.type, "source": ev.source, "task_id": ev.task_id, "payload": ev.payload}),
            Tool("questions_affected_by", "Dependency map: which questions depend on these fields or documents. "
                                          "Use it to keep redrafts narrow.",
                 {"field_ids": {"type": "array", "items": {"type": "string"}},
                  "doc_ids": {"type": "array", "items": {"type": "string"}}}, questions_for),
            Tool("list_questions", "All questionnaire question ids.", {},
                 lambda: [q["q_id"] for q in self.cfg["questionnaire"]["questions"]]),
            Tool("check_decision", "Dry run: the guardrail errors a proposed decision would get. [] means allowed.",
                 {"proposal": {"type": "object", "description": "the same object you would submit"}},
                 lambda proposal: check_decision(self.cfg, case, ev, now, proposal), ("proposal",)),
        ]
