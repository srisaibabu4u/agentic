"""Console output. Full detail always goes to out/audit_log.jsonl."""
from __future__ import annotations

import json
import sys

from .models import A1, A2, A3, A4, A5, AGENT_NAMES

TTY = sys.stdout.isatty()


def c(code, s):
    return f"\033[{code}m{s}\033[0m" if TTY else s


BOLD, DIM, BLUE, AMBER, GREEN, RED = "1", "2", "34", "33", "32", "31"


def ts(now):
    return now.strftime("%d %b %H:%M")


class Printer:
    def __init__(self, verbose=False):
        self.v = verbose

    def header(self, case):
        m = case.meta
        print(c(BOLD, f"\nKYC renewal  {case.case_id}  {m['legal_name']}"))
        print(c(DIM, f"prior rating {m['prior_rating']} · due {m['due_date']} · maker {m['maker']['name']} · "
                     f"checker {m['checker']['name']}\n"))

    def event(self, now, ev):
        who = {"maker": "MAKER", "checker": "CHECKER", "scheduler": "SCHEDULER"}.get(ev.source, ev.source)
        print(c(AMBER if ev.source in ("maker", "checker") else BOLD, f"[{ts(now)}] {who:<9} {ev.type}"))
        p = {k: v for k, v in ev.payload.items() if k not in ("case_id",)}
        if ev.type == "MAKER_SUBMIT":
            for d in p.get("uploads", []):
                print(f"           uploads {d['doc_id']} {d['type']}")
            for k, e in p.get("field_edits", {}).items():
                print(f"           confirms {k} = {e['value']}")
            for k, t in p.get("dispositions", {}).items():
                print(f"           dispositions {k}: {t}")
        elif ev.type == "CHECKER_RETURN":
            for r in p["returned"]:
                print(f"           returns {r['q_id']}: {r['comment']}")
        elif ev.type == "CHECKER_APPROVE":
            print(f"           approves rating {p['final_rating']}; next review {p.get('next_review')}")
        else:
            print(c(DIM, "           " + json.dumps(p)))

    def decision(self, now, case, d, before):
        if d.decision == "WAIT":
            print(c(DIM, f"           MASTER   wait · {d.rationale}"))
            return
        print(c(BLUE, f"           MASTER   {d.decision}") + c(DIM, f"  rules {','.join(d.rules_matched)}"
              + (f"  guardrails {','.join(d.guardrails_triggered)}" if d.guardrails_triggered else "")
              + (f"  status {before} → {d.new_status}" if before != d.new_status else "")))
        print(f"                    why: {d.rationale}")
        for t in d.tasks:
            dep = f" after {t.depends_on[0][-3:]}" if t.depends_on else ""
            scope = t.scope.get("docs") or t.scope.get("questions") or t.scope.get("template") or t.scope.get("checklist")
            mode = f" ({t.scope['mode']})" if "mode" in t.scope else ""
            print(c(BLUE, f"                    → {t.task_id[-3:]} {AGENT_NAMES[t.agent]}{mode}") + c(DIM, f" {scope}{dep}"))
        print()

    def agent(self, now, task, res, case):
        name = AGENT_NAMES[task.agent]
        print(c(BOLD, f"[{ts(now)}] {name}") + c(DIM, f"  {task.task_id[-3:]}"))
        ind = "           "
        if res.get("status") == "FAILED":
            print(c(RED, ind + "FAILED " + res.get("error", "")))
        elif task.agent == A1:
            for f in res["found"]:
                mark = c(GREEN, "✓") if f["fresh"] else c(RED, "stale")
                print(f"{ind}{f['doc_id']} {f['type']:<24} {f['doc_date']}  {mark}")
            for m in res["missing"]:
                print(c(RED, f"{ind}missing: {m['label']} · {m['reason']}"))
        elif task.agent == A2:
            for k, f in res["fields"].items():
                v = f["value"] if not isinstance(f["value"], list) else (f["value"] if len(str(f["value"])) < 60 else f"[{len(f['value'])} items]")
                conf = f"{f['confidence']:.2f}"
                print(f"{ind}{k:<24} {str(v)[:60]:<60} {c(RED, conf) if f['confidence'] < 0.8 else conf}  {c(DIM, f['source'])}")
            for fl in res["flags"]:
                print(c(AMBER, f"{ind}flag {fl['code']} {fl['ref']}"))
        elif task.agent == A3:
            for a in res["answers"]:
                fl = (" " + c(AMBER, ",".join(a["flags"]))) if a["flags"] else ""
                print(f"{ind}{a['q_id']} [{a['score'] if a['score'] is not None else '-'}] {a['answer']}{fl}")
                if a.get("evidence_needed"):
                    print(c(DIM, f"{ind}   needs: {'; '.join(a['evidence_needed'])}"))
            if res["mode"] != "suggest" and case.rating:
                r = case.rating
                print(c(DIM, f"{ind}rating {r['band']} · {r['working']} = {r['total']}/{r['max']}"))
        elif task.agent == A4:
            col = GREEN if res["verdict"] == "PASS" else RED
            print(c(col, f"{ind}{res['verdict']}") + c(DIM, f"  checks {res.get('checks_summary')}"))
            for f in res["findings"]:
                print(c(RED if f["severity"] == "HIGH" else AMBER,
                        f"{ind}{f['severity']} {f['q_id']} {f['check']}: {f['issue']}"
                        f" (policy {f['policy_ref']}, evidence on file: {f['evidence_available']})"))
        elif task.agent == A5:
            for s in res["sent"]:
                cc = f" cc {', '.join(s['cc'])}" if s.get("cc") else ""
                print(f"{ind}to {s['to']}{cc}")
                print(c(DIM, f"{ind}subject: {s['subject']}"))
                if self.v:
                    for line in s["body"].splitlines():
                        print(c(DIM, f"{ind}  | {line}"))
        if self.v:
            print(c(DIM, ind + json.dumps(res, default=str)[:1500]))

    def shadow(self, agree, llm_agents, why):
        mark = c(GREEN, "agrees") if agree else c(RED, f"DISAGREES (LLM wanted {llm_agents})")
        print(c(DIM, f"                    LLM shadow master {mark}: {why[:160]}"))

    def stuck(self, case):
        print(c(RED, f"\nNo agent work pending and no scripted human action for status {case.status}. Stopping."))

    def summary(self, case, audit, outbox, out):
        n_dec = sum(1 for a in audit if a["kind"] == "master_decision")
        n_task = sum(1 for a in audit if a["kind"] == "agent_result")
        n_h = sum(1 for a in audit if a["kind"] == "event" and a["source"] in ("maker", "checker"))
        print(c(BOLD, "\n──────── result"))
        if case.final:
            f = case.final
            print(f"status {case.status} · rating {f['rating']} ({case.rating['total']}/{case.rating['max']}) · "
                  f"approved by {f['approved_by']} · next review {f['next_review']}")
        else:
            print(f"status {case.status}")
        print(f"{n_dec} master decisions · {n_task} agent tasks · {n_h} human actions · {len(outbox)} notifications")
        print(c(DIM, f"files: {out}/audit_log.jsonl, final_case.json, outbox.json\n"))
