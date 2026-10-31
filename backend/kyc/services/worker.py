"""The worker: runs master decisions and agent tasks from the database.

    step(db)            do one unit of work; False when there is nothing to do
    run_until_idle(db)  step until nothing is left (tests, demos)

One unit of work is either
  * a master decision on an undecided event (engine.process_next), or
  * one agent task: claim -> run -> record.

Claim and record are short transactions; the agent itself runs outside any
lock, so a slow live (Claude) call never blocks other cases. A task is ready
when every task it depends on has finished and its case has no undecided
events, so the master always reacts before the next agent starts, as in the
in-memory Engine. Notifications go first, also as in the Engine.
"""
from __future__ import annotations

import os
import socket
import traceback
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import and_, any_, exists, func, not_, select
from sqlalchemy.orm import Session, aliased

from orchestrator import state
from orchestrator.agents import build_agents
from orchestrator.engine import AGENT_MINUTES
from orchestrator.models import A3, A5, Task

from ..db import models as m
from . import engine, store

Clock = Callable[[Session, m.Case, m.AgentTask], datetime]
FINISHED = ("DONE", "FAILED")


def wall_clock(db: Session, row: m.Case, task: m.AgentTask) -> datetime:
    return store.local(datetime.now(timezone.utc))


def sim_clock(db: Session, row: m.Case, task: m.AgentTask) -> datetime:
    """The prototype's simulated time: each agent takes a fixed number of minutes after the case's last event."""
    last = db.scalar(select(func.max(m.CaseEvent.occurred_at)).where(m.CaseEvent.case_id == row.id))
    return store.local(last) + timedelta(minutes=AGENT_MINUTES[task.agent])


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


# ============================================================== claim
def claim_task(db: Session, name: str) -> m.AgentTask | None:
    dep = aliased(m.AgentTask)
    t = m.AgentTask
    ready = (
        select(t).where(
            t.status == "PENDING",
            not_(exists().where(m.CaseEvent.case_id == t.case_id, m.CaseEvent.processed_at.is_(None))),
            not_(exists().where(and_(dep.case_id == t.case_id, dep.task_ref == any_(t.depends_on),
                                     dep.status.not_in(FINISHED)))),
        )
        .order_by(t.agent != A5, t.id)
        .limit(1)
        .with_for_update(of=t, skip_locked=True)
    )
    task = db.scalars(ready).first()
    if task is None:
        return None
    task.status, task.claimed_by, task.claimed_at = "RUNNING", name, func.now()
    db.commit()
    return task


# ============================================================== run + record
def execute_task(db: Session, task: m.AgentTask, clock: Clock = wall_clock, llm=None,
                 live_agents: frozenset[str] = frozenset()) -> dict:
    row = db.get(m.Case, task.case_id)
    cfg = store.load_config(db, row)
    case = store.load_case(db, row, cfg)
    now = clock(db, row, task)
    live = task.agent in live_agents and llm is not None
    agent = build_agents(cfg, llm if live else None, {task.agent} if live else set())[task.agent]
    proto_task = Task(task.task_ref, task.agent, task.scope, task.inputs, list(task.depends_on), task.retry)
    db.commit()  # end the read transaction before the (possibly slow) agent call

    try:
        res = agent.run(proto_task, case, now)
    except Exception as exc:  # an agent crash is a FAILED result; the master's retry rule (R3a, G4) takes over
        res = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}", "scope": task.scope, "inputs": task.inputs,
               "trace": traceback.format_exc(limit=3)}

    record_result(db, task.id, res, now, live)
    return res


def record_result(db: Session, task_id: int, res: dict, now: datetime, live: bool = False) -> None:
    task = db.get(m.AgentTask, task_id)
    row = db.get(m.Case, task.case_id, with_for_update=True)
    cfg = store.load_config(db, row)
    case = store.load_case(db, row, cfg)

    state.apply_result(case, cfg, task.agent, res)
    case.pending = [p for p in case.pending if p.task_id != task.task_ref]
    failed = res.get("status") == "FAILED"
    task.status, task.result, task.finished_at, task.live = ("FAILED" if failed else "DONE"), res, func.now(), live
    task.error = res.get("error") if failed else None
    store.save_case(db, row, case, task_id=task.id)

    if not failed and task.agent == A3 and res.get("mode") == "suggest":
        store.save_suggestions(db, row, res["answers"], task.id)
    if not failed and task.agent == A5:
        _save_notifications(db, row, task, res)
    engine.add_event(db, row, "AGENT_RESULT", task.agent, res, now, task_id=task.id)
    db.commit()


def _save_notifications(db: Session, row: m.Case, task: m.AgentTask, res: dict) -> None:
    users = {u.username: u.id for u in db.scalars(select(m.User))}
    to = task.inputs.get("to", [])
    cc = [users[c] for c in [task.inputs.get("escalate_cc")] if c in users]
    for i, msg in enumerate(res.get("sent", [])):
        to_id = users.get(to[i]) if i < len(to) else None
        if to_id is None:
            continue
        db.add(m.Notification(case_id=row.id, task_id=task.id, to_user_id=to_id, cc_user_ids=cc,
                              template=task.inputs.get("template", "?"), channel=msg.get("channel", "task_inbox"),
                              subject=msg["subject"], body=msg["body"], message_id=msg.get("message_id")))


# ============================================================== loop
def step(db: Session, clock: Clock = wall_clock, name: str | None = None, llm=None,
         live_agents: frozenset[str] = frozenset()) -> bool:
    if engine.process_next(db) is not None:
        db.commit()
        return True
    task = claim_task(db, name or worker_name())
    if task is None:
        db.rollback()
        return False
    execute_task(db, task, clock, llm, live_agents)
    return True


def run_until_idle(db: Session, clock: Clock = wall_clock, max_steps: int = 100_000, **kw) -> int:
    n = 0
    while n < max_steps and step(db, clock, **kw):
        n += 1
    return n
