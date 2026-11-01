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

from .agents import build_agents
from .master import Master
from .master_agent import AgenticMaster
from .models import A1, A2, A3, A4, A5, AGENT_NAMES, Case, Event
from . import report, state

AGENT_MINUTES = {A1: 1, A2: 4, A3: 3, A4: 2, A5: 0}


class Engine:
    def __init__(self, cfg: dict, out_dir: Path, llm=None, live_agents: set | None = None,
                 live_master: bool = False, verbose: bool = False):
        self.cfg, self.out = cfg, out_dir
        m = cfg["case"]
        self.case = Case(m["case_id"], m)
        self.master = AgenticMaster(cfg, llm) if live_master and llm else Master(cfg)
        self.agents = build_agents(cfg, llm, live_agents or set())
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

    # -------------------------------------------------------------- state changes (rules in state.py)
    def apply_result(self, agent: str, task, res: dict):
        state.apply_result(self.case, self.cfg, agent, res)
        if res.get("status") == "FAILED":
            return
        if agent == A3 and res["mode"] == "suggest":
            for a in res["answers"]:
                self.suggestions[a["q_id"]] = a
        elif agent == A5:
            self.outbox += res["sent"]

    def apply_human(self, ev: Event):
        state.apply_human(self.case, ev, self.now)

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
        if decision.meta.get("decided_by") in ("master_agent", "playbook_fallback"):
            self.r.shadow(decision.meta.get("agrees_with_playbook", True),
                          decision.meta.get("playbook", {}).get("agents", []), decision.meta["decided_by"])

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
