"""The database-backed engine must see exactly what the in-memory engine sees.

* load_config rebuilds the prototype's reference data from the tables;
* save_case + load_case round-trip a Case without loss, at every point where
  the Halcyon run waits on a person;
* the master step on the database matches the in-memory master for every seeded case.
"""
from __future__ import annotations

import io
import contextlib
from dataclasses import asdict

import pytest
from sqlalchemy import func, select

from orchestrator.engine import Engine

from kyc.db import models as m
from kyc.seed.reference import load_prototype
from kyc.services import engine as db_engine
from kyc.services import store

PROTO = load_prototype()


@pytest.fixture(autouse=True)
def tmp_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "UPLOADS_DIR", tmp_path)


def halcyon(db) -> m.Case:
    return db.scalar(select(m.Case).where(m.Case.case_ref == "RA-2026-0412"))


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def test_config_matches_prototype(db):
    cfg = store.load_config(db, halcyon(db))
    assert cfg["checklist"] == PROTO["checklist"]
    assert cfg["dependency_map"] == PROTO["dependency_map"]
    assert cfg["document_store"] == PROTO["document_store"]
    q, pq = cfg["questionnaire"], PROTO["questionnaire"]
    for k in ("id", "questions", "rating_bands", "policy", "industry_risk"):
        assert q[k] == pq[k], k
    assert q["country_risk"].items() >= pq["country_risk"].items()


def test_round_trip_at_every_human_stop(db, tmp_path):
    row = halcyon(db)
    e = Engine(PROTO, tmp_path)
    quiet(e.start)
    stops = []
    while True:
        stop = quiet(e.advance, use_script=False)
        store.save_case(db, row, e.case)
        db.expire_all()
        loaded = store.load_case(db, row, store.load_config(db, row))
        assert asdict(loaded) == asdict(e.case), f"round trip differs at stop {len(stops) + 1} ({e.case.status})"
        stops.append(e.case.status)
        if stop == "CLOSED":
            break
        e.queue.append(e.next_human_event())
    # maker, checker, maker (after return), checker, closed
    assert stops == ["WITH_MAKER", "WITH_CHECKER", "RETURNED", "WITH_CHECKER", "CLOSED"]
    assert row.final_rating == "MEDIUM" and row.closed_at is not None
    versions = db.scalar(select(func.max(m.Answer.version)).where(m.Answer.case_id == row.id, m.Answer.q_id == "Q7"))
    assert versions >= 2, "Q7 was redrafted, so it must have more than one version"


def test_master_step_for_every_seeded_case(db, tmp_path):
    n_cases = db.scalar(select(func.count()).select_from(m.Case))
    decisions = []
    while (md := db_engine.process_next(db)) is not None:
        decisions.append(md)
    assert len(decisions) == n_cases
    assert all(d.rules_matched == ["R1"] and d.new_status == "DISCOVERY" for d in decisions)
    assert db.scalar(select(func.count()).select_from(m.CaseEvent).where(m.CaseEvent.processed_at.is_(None))) == 0
    tasks = list(db.scalars(select(m.AgentTask)))
    assert len(tasks) == n_cases and {t.agent for t in tasks} == {"A1_DISCOVERY"}
    assert {t.status for t in tasks} == {"PENDING"}

    # Halcyon: same decision and same task as the in-memory engine
    e = Engine(PROTO, tmp_path)
    quiet(e.start)
    quiet(e.handle, e.queue.popleft())
    row = halcyon(db)
    loaded = store.load_case(db, row, store.load_config(db, row))
    assert loaded.pending == e.case.pending
    assert loaded.status == e.case.status == "DISCOVERY"
    md = next(d for d in decisions if d.case_id == row.id)
    want = e.audit[-1]
    assert md.rationale == want["decision"]["rationale"]
    assert md.state_snapshot == want["state"]


def test_second_engine_skips_a_locked_case(db):
    """Two engines never decide on the same case at once: the locked case is skipped."""
    from kyc.db import engine as sa_engine
    from kyc.db.session import SessionLocal

    first = db_engine.next_event_case(db)  # this transaction now holds the lock
    with SessionLocal(bind=sa_engine) as other:
        second = db_engine.next_event_case(other)
        other.rollback()
    assert first is not None and second is not None and second != first
