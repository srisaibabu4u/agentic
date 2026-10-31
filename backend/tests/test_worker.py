"""End to end on the database: the worker runs every seeded case.

Halcyon is driven by its scripted maker and checker and must match the
in-memory Engine exactly: every master decision (with the state it read and
its timestamp), every agent result, every notification and the final case.
Every generated case must stop where its scenario says.
"""
from __future__ import annotations

import contextlib
import io
from collections import deque
from dataclasses import asdict
from datetime import datetime

import pytest
from sqlalchemy import any_, func, select, update

from orchestrator import agents as proto_agents
from orchestrator.engine import Engine

from kyc.db import models as m
from kyc.seed.reference import load_prototype
from kyc.services import engine as db_engine
from kyc.services import store, worker

from test_agents_general import EXPECTED_STOP

PROTO = load_prototype()


@pytest.fixture(autouse=True)
def tmp_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "UPLOADS_DIR", tmp_path)


def in_memory_run(tmp_path) -> Engine:
    e = Engine(PROTO, tmp_path / "mem")
    with contextlib.redirect_stdout(io.StringIO()):
        e.run()
    return e


def only_halcyon(db) -> m.Case:
    """Set the other seeded cases aside (inside the rolled-back test transaction) so the worker runs Halcyon alone."""
    row = db.scalar(select(m.Case).where(m.Case.case_ref == "RA-2026-0412"))
    db.execute(update(m.CaseEvent).where(m.CaseEvent.case_id != row.id).values(processed_at=func.now()))
    return row


def drive(db, script: dict) -> None:
    """Run the worker; whenever Halcyon waits on a person, play that person's next scripted step."""
    row = db.scalar(select(m.Case).where(m.Case.case_ref == "RA-2026-0412"))
    script = {k: deque(v) for k, v in script.items()}
    while True:
        worker.run_until_idle(db, worker.sim_clock)
        db.refresh(row)
        if row.status == "CLOSED" or not script.get(row.waiting_on):
            return
        step = dict(script[row.waiting_on].popleft())
        last = db.scalar(select(func.max(m.CaseEvent.occurred_at)).where(m.CaseEvent.case_id == row.id))
        at = max(store.local(last), datetime.fromisoformat(step.pop("at")))
        actor = row.maker_id if row.waiting_on == "maker" else row.checker_id
        db_engine.add_event(db, row, step.pop("event"), row.waiting_on, step, at, actor_id=actor)
        db.commit()


def db_audit(db, row) -> tuple[list, list, list]:
    """The database run, in the shape of the in-memory audit log: decisions, agent results, human events."""
    decisions, results, humans = [], [], []
    for md in db.scalars(select(m.MasterDecision).where(m.MasterDecision.case_id == row.id).order_by(m.MasterDecision.id)):
        ev = db.get(m.CaseEvent, md.event_id)
        tasks = db.scalars(select(m.AgentTask).where(m.AgentTask.decision_id == md.id).order_by(m.AgentTask.id))
        decisions.append({
            "t": store.local(md.decided_at).isoformat(),
            "trigger": ev.type + (":" + ev.source if ev.type == "AGENT_RESULT" else ""),
            "state": md.state_snapshot,
            "decision": {"decision": md.decision, "new_status": md.new_status,
                         "tasks": [{"task_id": t.task_ref, "agent": t.agent, "scope": t.scope, "inputs": t.inputs,
                                    "depends_on": list(t.depends_on), "retry": t.retry} for t in tasks],
                         "rules_matched": list(md.rules_matched), "guardrails_triggered": list(md.guardrails_triggered),
                         "rationale": md.rationale}})
    for ev in db.scalars(select(m.CaseEvent).where(m.CaseEvent.case_id == row.id).order_by(m.CaseEvent.seq)):
        if ev.type == "AGENT_RESULT":
            results.append({"agent": ev.source, "result": ev.payload})
        elif ev.type != "RENEWAL_DUE":
            humans.append({"event": ev.type, "source": ev.source, "payload": ev.payload})
    return decisions, results, humans


def test_halcyon_end_to_end_matches_in_memory(db, tmp_path):
    e = in_memory_run(tmp_path)
    row = only_halcyon(db)
    drive(db, PROTO["human_script"])
    decisions, results, humans = db_audit(db, row)

    want_decisions = [{k: r[k] for k in ("t", "trigger", "state", "decision")} for r in e.audit if r["kind"] == "master_decision"]
    want_results = [{"agent": r["agent"], "result": r["result"]} for r in e.audit if r["kind"] == "agent_result"]
    want_humans = [{"event": r["event"], "source": r["source"], "payload": r["payload"]} for r in e.audit
                   if r["kind"] == "event" and r["event"] != "RENEWAL_DUE"]
    assert len(decisions) == len(want_decisions)
    for i, (got, want) in enumerate(zip(decisions, want_decisions)):
        assert got == want, f"decision {i + 1} ({want['trigger']}) differs"
    assert results == want_results
    assert humans == want_humans

    outbox = []
    for n in db.scalars(select(m.Notification).where(m.Notification.case_id == row.id).order_by(m.Notification.id)):
        to = db.get(m.User, n.to_user_id)
        outbox.append({"channel": n.channel, "to": f"{to.full_name} · {to.title}",
                       "cc": [db.get(m.User, c).full_name for c in n.cc_user_ids], "subject": n.subject,
                       "body": n.body, "message_id": n.message_id})
    assert outbox == e.outbox

    case = store.load_case(db, row, store.load_config(db, row))
    assert asdict(case) == asdict(e.case)
    assert (row.status, row.final_rating, row.waiting_on) == ("CLOSED", "MEDIUM", None)


def test_every_generated_case_stops_where_its_scenario_says(db):
    worker.run_until_idle(db, worker.sim_clock)
    rows = list(db.scalars(select(m.Case).where(m.Case.scenario != "halcyon_sample")))
    assert rows
    for row in rows:
        last = db.scalars(select(m.MasterDecision).where(m.MasterDecision.case_id == row.id,
                                                         m.MasterDecision.decision != "WAIT")
                          .order_by(m.MasterDecision.id.desc())).first()
        assert row.waiting_on == "maker", (row.case_ref, row.status)
        assert EXPECTED_STOP[row.scenario] in last.rules_matched, (row.case_ref, row.scenario, last.rules_matched)
        assert ("G6" in last.guardrails_triggered) == (row.scenario == "near_due"), row.case_ref
    assert db.scalar(select(func.count()).select_from(m.AgentTask).where(m.AgentTask.status.in_(("PENDING", "RUNNING")))) == 0


def test_agent_crash_is_retried_by_the_master(db, monkeypatch):
    real = proto_agents.IngestionAgent.offline
    calls = {"n": 0}

    def flaky(self, task, case, now):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("document service timed out")
        return real(self, task, case, now)

    monkeypatch.setattr(proto_agents.IngestionAgent, "offline", flaky)
    row = only_halcyon(db)
    worker.run_until_idle(db, worker.sim_clock)

    a2 = list(db.scalars(select(m.AgentTask).where(m.AgentTask.case_id == row.id, m.AgentTask.agent == "A2_INGEST")
                         .order_by(m.AgentTask.id)))
    assert [t.status for t in a2[:2]] == ["FAILED", "DONE"]
    assert "timed out" in a2[0].error and a2[1].retry == 1
    retry = db.scalars(select(m.MasterDecision).where(m.MasterDecision.case_id == row.id,
                                                      "R3a" == any_(m.MasterDecision.rules_matched))).one()
    assert "retry 1" in retry.rationale
    db.refresh(row)
    assert row.waiting_on == "maker"  # the case carried on normally after the retry
