"""Event loop: the only place that writes to the case.

    event -> master.decide -> tasks queued -> agent runs -> result event -> master ...

Humans are simulated from the scenario's human_script: whenever no agent work
is pending and the case is waiting on the maker or checker, the next scripted
action for that role is played as an event.
"""
from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from .agents import build_agents, compute_rating
from .master import Master
from .models import A1, A2, A3, A4, A5, AGENT_NAMES, Case, Event
from . import report

AGENT_MINUTES = {A1: 1, A2: 4, A3: 3, A4: 2, A5: 0}


class Engine:
    def __init__(self, cfg: dict, out_dir: Path, llm=None, live_agents: set | None = None,
                 shadow_master: bool = False, verbose: bool = False):
        self.cfg, self.out = cfg, out_dir
        m = cfg["case"]
        self.case = Case(m["case_id"], m)
        self.master = Master(cfg)
        self.agents = build_agents(cfg, llm, live_agents or set())
        self.llm, self.shadow = llm, shadow_master
        self.now = datetime.fromisoformat(m["start_time"])
        self.queue: deque[Event] = deque()
        self.done: set[str] = set()
        self.audit: list[dict] = []
        self.outbox: list[dict] = []
        self.suggestions: dict = {}
        self.human = {k: deque(v) for k, v in cfg["human_script"].items()}
        self.r = report.Printer(verbose)

    # -------------------------------------------------------------- audit
    def log(self, kind: str, **data):
        rec = {"t": self.now.isoformat(), "kind": kind, "case_status": self.case.status, **data}
        self.audit.append(rec)

    # -------------------------------------------------------------- state changes
    def apply_result(self, agent: str, task, res: dict):
        c = self.case
        if res.get("status") == "FAILED":
            return
        if agent == A1:
            c.manifest = res
            store = {d["doc_id"]: d for d in self.cfg["document_store"]} | c.docs
            for f in res["found"]:
                c.docs[f["doc_id"]] = {**store[f["doc_id"]], "fresh": f["fresh"]}
        elif agent == A2:
            for fid, f in res["fields"].items():
                if c.fields.get(fid, {}).get("source") == "maker":
                    continue  # never overwrite a value the maker confirmed
                c.fields[fid] = f
            for fl in res["flags"]:
                if fl["code"] == "LOW_CONF_FIELD":
                    conf = res["fields"][fl["ref"]]["confidence"]
                    c.open_items[fl["ref"]] = f"LOW_CONF_FIELD ({conf}): confirm value"
                elif fl["code"] == "SCREENING_HIT" and fl["ref"] not in c.maker_inputs["dispositions"]:
                    c.open_items[fl["ref"]] = "SCREENING_HIT: disposition required"
        elif agent == A3:
            if res["mode"] == "suggest":
                for a in res["answers"]:
                    self.suggestions[a["q_id"]] = a
            else:
                for a in res["answers"]:
                    c.answers[a["q_id"]] = a
                    for r in c.checker_returns:
                        if r["q_id"] == a["q_id"] and res["mode"] == "redraft" and not r.get("resolved"):
                            r["resolved"] = True
                c.rating = compute_rating(c.answers, self.cfg["questionnaire"]["rating_bands"])
        elif agent == A4:
            c.findings = res["findings"]
        elif agent == A5:
            self.outbox += res["sent"]

    def apply_human(self, ev: Event):
        c, p = self.case, ev.payload
        if ev.type == "MAKER_SUBMIT":
            for d in p.get("uploads", []):
                c.docs[d["doc_id"]] = {**d, "fresh": True}
            for fid, e in p.get("field_edits", {}).items():
                c.fields[fid] = {**c.fields.get(fid, {}), "value": e["value"], "confidence": 1.0,
                                 "source": "maker", "note": e["note"]}
                c.maker_inputs["edits"][fid] = e
                c.open_items.pop(fid, None)
            for ref, text in p.get("dispositions", {}).items():
                c.maker_inputs["dispositions"][ref] = text
                c.open_items.pop(ref, None)
            c.maker_submitted = True
        elif ev.type == "CHECKER_RETURN":
            c.checker_returns += [dict(r) for r in p["returned"]]
            c.maker_submitted = False
        elif ev.type == "CHECKER_APPROVE":
            months = {"LOW": 36, "MEDIUM": 12, "HIGH": 6}[p["final_rating"]]
            due = datetime.fromisoformat(c.meta["due_date"])
            nxt = due.replace(year=due.year + months // 12) if months >= 12 else due + timedelta(days=182)
            p["next_review"] = nxt.date().isoformat()
            c.final = {"rating": p["final_rating"], "score": c.rating, "approved_by": c.meta["checker"]["name"],
                       "approved_at": self.now.isoformat(), "next_review": p["next_review"], "comment": p.get("comment")}

    # -------------------------------------------------------------- loop
    def handle(self, ev: Event):
        c = self.case
        if ev.type != "AGENT_RESULT":
            self.r.event(self.now, ev)
            self.log("event", event=ev.type, source=ev.source, payload=ev.payload)
        decision = self.master.decide(c, ev, self.now)
        before = c.status
        c.status = decision.new_status
        c.pending += decision.tasks
        self.r.decision(self.now, c, decision, before)
        self.log("master_decision", trigger=ev.type + (":" + ev.source if ev.type == "AGENT_RESULT" else ""),
                 state=c.snapshot(), decision=decision.to_dict())
        if self.shadow and self.llm and decision.decision != "WAIT":
            self.shadow_check(ev, decision)

    def shadow_check(self, ev: Event, decision):
        """Ask the LLM master the same question and record whether it agrees with the rules."""
        out = self.llm.call_json("master.md", {"case_record": self.case.snapshot(), "event": ev.__dict__,
                                               "dependency_map": self.cfg["dependency_map"]},
                                 ("decision", "tasks"), bank_name="Example Bank")
        rules_agents = sorted(t.agent for t in decision.tasks)
        llm_agents = sorted(t.get("agent", "?") for t in out.get("tasks", []))
        agree = rules_agents == llm_agents
        self.r.shadow(agree, llm_agents, out.get("rationale", ""))
        self.log("master_shadow", agrees=agree, llm=out)

    def next_ready_task(self):
        ready = [t for t in self.case.pending if all(d in self.done for d in t.depends_on)]
        ready.sort(key=lambda t: t.agent != A5)  # notifications go out first; they are fire-and-forget
        return ready[0] if ready else None

    def run_task(self, task):
        agent = self.agents[task.agent]
        self.now += timedelta(minutes=AGENT_MINUTES[task.agent])
        res = agent.run(task, self.case, self.now)
        self.case.pending.remove(task)
        self.done.add(task.task_id)
        self.apply_result(task.agent, task, res)
        self.r.agent(self.now, task, res, self.case)
        self.log("agent_result", task=task.__dict__, agent=task.agent,
                 mode="live" if agent.llm else "offline", result=res)
        self.queue.append(Event("AGENT_RESULT", task.agent, res, task.task_id))

    WAITING_ON = {"WITH_MAKER": "maker", "RETURNED": "maker", "WITH_CHECKER": "checker"}

    def waiting_on(self) -> str | None:
        """Which human the case is waiting for, if no agent work is left."""
        if self.queue or self.next_ready_task():
            return None
        return self.WAITING_ON.get(self.case.status)

    def next_human_event(self):
        role = self.WAITING_ON.get(self.case.status)
        if not role or not self.human[role]:
            return None
        step = dict(self.human[role].popleft())
        self.now = max(self.now, datetime.fromisoformat(step.pop("at")))
        return Event(step.pop("event"), role, step)

    def start(self):
        self.r.header(self.case)
        self.queue.append(Event("RENEWAL_DUE", "scheduler",
                                {"case_id": self.case.case_id, "due": self.case.meta["due_date"]}))

    def advance(self, use_script: bool = True, max_steps: int = 200) -> str:
        """Run agents until the case closes or needs a human. Returns the stop reason."""
        for _ in range(max_steps):
            if self.queue:
                ev = self.queue.popleft()
                if ev.type != "AGENT_RESULT":
                    self.apply_human(ev)
                self.handle(ev)
                continue
            t = self.next_ready_task()
            if t:
                self.run_task(t)
                continue
            if self.case.status == "CLOSED":
                return "CLOSED"
            if use_script:
                ev = self.next_human_event()
                if ev:
                    self.queue.append(ev)
                    continue
            role = self.WAITING_ON.get(self.case.status)
            if role:
                return f"WAITING_ON_{role.upper()}"
            self.r.stuck(self.case)
            return "STUCK"
        return "MAX_STEPS"

    def submit_human(self, role: str, event_type: str, payload: dict, at: datetime | None = None):
        """Queue a maker or checker action (used by the interactive / MCP mode)."""
        expected = self.waiting_on()
        if expected != role:
            raise ValueError(f"Case is in status {self.case.status} and is waiting on "
                             f"{expected or 'agents, not a human'}; a {role} action is not allowed now.")
        self.now = max(self.now + timedelta(hours=1), at) if at else self.now + timedelta(hours=1)
        self.queue.append(Event(event_type, role, payload))

    def run(self, max_steps: int = 200):
        self.start()
        self.advance(use_script=True, max_steps=max_steps)
        self.finish()

    def finish(self):
        self.out.mkdir(parents=True, exist_ok=True)
        with open(self.out / "audit_log.jsonl", "w") as f:
            for rec in self.audit:
                f.write(json.dumps(rec, default=str) + "\n")
        c = self.case
        final = {"case_id": c.case_id, "status": c.status, "final": c.final, "rating": c.rating,
                 "answers": c.answers, "fields": c.fields, "maker_inputs": c.maker_inputs,
                 "checker_returns": c.checker_returns, "documents": {k: {x: v[x] for x in ("type", "date", "source")}
                                                                       for k, v in c.docs.items()}}
        (self.out / "final_case.json").write_text(json.dumps(final, indent=2, default=str))
        (self.out / "outbox.json").write_text(json.dumps(self.outbox, indent=2))
        self.r.summary(c, self.audit, self.outbox, self.out)
