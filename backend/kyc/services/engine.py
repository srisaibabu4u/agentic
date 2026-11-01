"""Database-backed master step: one event in, one decision out, in one transaction.

    process_next(db)   pick the oldest unprocessed event of any case and decide on it
    add_event(db, ...) append an event to a case's log (agent results, maker/checker actions)

The steps mirror orchestrator.engine.Engine.handle():

1. lock the case row (SELECT ... FOR UPDATE), so only one decision runs per case at a time;
2. rebuild the Case from the tables (store.load_case);
3. for a human event, apply it to the case (orchestrator.state.apply_human);
4. decide: Master (the rule playbook) or, with a live master, AgenticMaster (Claude decides,
   code enforces the guardrails). The case stays locked while it decides, so a slow model call
   holds up only this case; other engines skip it and work on other cases;
5. give approved lessons to the drafting and review tasks they concern (orchestrator.lessons);
6. store the decision, queue its tasks, update the case, mark the event processed;
7. when the decision closes the case, propose lessons from it for a team lead to approve.

Agent results are applied to the case by the worker when the agent finishes
(step 3), exactly like Engine.run_task, so by the time the master sees an
AGENT_RESULT event the case already contains it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator import lessons, state
from orchestrator.master import Master
from orchestrator.master_agent import AgenticMaster
from orchestrator.models import Event

from ..db import models as m
from . import store


def add_event(db: Session, row: m.Case, type_: str, source: str, payload: dict, occurred_at: datetime,
              actor_id: int | None = None, task_id: int | None = None) -> m.CaseEvent:
    seq = (db.scalar(select(func.max(m.CaseEvent.seq)).where(m.CaseEvent.case_id == row.id)) or 0) + 1
    ev = m.CaseEvent(case_id=row.id, seq=seq, type=type_, source=source, payload=payload,
                     occurred_at=occurred_at, actor_id=actor_id, task_id=task_id)
    db.add(ev)
    db.flush()
    return ev


def next_event_case(db: Session) -> int | None:
    """A case with work for the master, locked for this transaction; other engines skip it."""
    return db.scalar(
        select(m.Case.id).where(m.Case.id.in_(select(m.CaseEvent.case_id).where(m.CaseEvent.processed_at.is_(None))))
        .order_by(m.Case.id).limit(1).with_for_update(skip_locked=True))


def process_next(db: Session, llm=None) -> m.MasterDecision | None:
    """llm: when given, the master agent decides (live master); otherwise the rule playbook."""
    case_id = next_event_case(db)
    if case_id is None:
        return None
    ev = db.scalars(select(m.CaseEvent).where(m.CaseEvent.case_id == case_id, m.CaseEvent.processed_at.is_(None))
                    .order_by(m.CaseEvent.seq).limit(1)).one()
    return process_event(db, ev, llm)


def process_event(db: Session, ev: m.CaseEvent, llm=None) -> m.MasterDecision:
    row = db.get(m.Case, ev.case_id, with_for_update=True)
    cfg = store.load_config(db, row)
    case = store.load_case(db, row, cfg)
    now = store.local(ev.occurred_at)
    task_ref = db.get(m.AgentTask, ev.task_id).task_ref if ev.task_id else None
    event = Event(ev.type, ev.source, dict(ev.payload), task_ref)

    if ev.type != "AGENT_RESULT":
        state.apply_human(case, event, now)
        ev.payload = event.payload  # e.g. CHECKER_APPROVE gains next_review, as in the in-memory audit log

    prev = case.status
    decision = (AgenticMaster(cfg, llm) if llm else Master(cfg)).decide(case, event, now)
    case.status = decision.new_status
    store.mark_lessons_used(db, lessons.attach(decision.tasks, cfg["lessons"]))
    case.pending += decision.tasks

    md = m.MasterDecision(case_id=row.id, event_id=ev.id, decision=decision.decision, prev_status=prev,
                          new_status=decision.new_status, rules_matched=decision.rules_matched,
                          guardrails_triggered=decision.guardrails_triggered, rationale=decision.rationale,
                          state_snapshot=case.snapshot(), config_version=store.config_version(cfg), decided_at=now,
                          shadow=decision.meta or None)  # master agent: who decided, its trace, the playbook's view
    db.add(md)
    db.flush()
    store.save_case(db, row, case, decision_id=md.id, actor_id=ev.actor_id)
    if decision.decision == "CLOSE":
        store.propose_lessons(db, row)
    ev.processed_at = datetime.now(timezone.utc)
    db.flush()
    return md
