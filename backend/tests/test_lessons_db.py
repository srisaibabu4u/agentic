"""Lessons on the database: proposed when a case closes, reviewed by a team lead, used by the next cases."""
from __future__ import annotations

from sqlalchemy import select

from kyc.db import models as m
from kyc.seed.reference import load_prototype
from kyc.services import store

from test_api import as_, client  # noqa: F401  (fixture)
from test_worker import drive, only_halcyon, tmp_uploads  # noqa: F401  (fixture)

PROTO = load_prototype()
Q6_KEY = "finding:Q6:C2:7.2.4"


def close_halcyon(db) -> m.Case:
    only_halcyon(db)
    drive(db, PROTO["human_script"])
    row = db.scalar(select(m.Case).where(m.Case.case_ref == "RA-2026-0412"))
    assert row.status == "CLOSED"
    return row


def test_closing_a_case_proposes_its_lessons(db):
    close_halcyon(db)
    got = {x.key: x for x in db.scalars(select(m.Lesson))}
    assert len(got) == 2 and Q6_KEY in got
    assert all(x.status == "PROPOSED" and x.evidence[0]["case_ref"] == "RA-2026-0412" for x in got.values())


def test_the_same_problem_again_adds_evidence_not_a_new_lesson(db):
    row = close_halcyon(db)
    store.propose_lessons(db, row)  # the same case again changes nothing
    other = db.scalar(select(m.Case).where(m.Case.case_ref != row.case_ref).order_by(m.Case.id))
    db.add(m.ReviewFinding(case_id=other.id, q_id="Q6", check_code="C2", severity="MEDIUM", issue="Name the list.",
                           policy_ref="7.2.4", evidence_available=True))
    store.propose_lessons(db, other)
    x = db.scalar(select(m.Lesson).where(m.Lesson.key == Q6_KEY))
    assert x.occurrences == 2 and [e["case_ref"] for e in x.evidence] == ["RA-2026-0412", other.case_ref]
    assert db.scalar(select(m.Lesson).where(m.Lesson.key != Q6_KEY)).occurrences == 1


def test_only_a_team_lead_reviews_lessons(db, client):  # noqa: F811
    close_halcyon(db)
    ref = store.lesson_ref(db.scalar(select(m.Lesson).where(m.Lesson.key == Q6_KEY)).id)
    assert client.post(f"/lessons/{ref}/approve", json={}, headers=as_("priya.nair")).status_code == 403
    r = client.post(f"/lessons/{ref}/approve", json={"text": "Q6: name the screening list and the record ID (policy 7.2.4)."},
                    headers=as_("grace.lim"))
    assert r.status_code == 200 and r.json()["status"] == "APPROVED" and r.json()["reviewed_by"] == "Grace Lim"
    assert client.post(f"/lessons/{ref}/approve", json={}, headers=as_("grace.lim")).status_code == 409
    assert client.post(f"/lessons/{ref}/retire", json={}, headers=as_("grace.lim")).status_code == 422  # needs a note
    approved = client.get("/lessons?status=APPROVED", headers=as_("daniel.tan")).json()
    assert [x["id"] for x in approved] == [ref] and approved[0]["text"].startswith("Q6: name the screening list")
    assert client.post(f"/lessons/{ref}/retire", json={"note": "now in the policy text"},
                       headers=as_("grace.lim")).json()["status"] == "RETIRED"
    assert client.post("/lessons/L-9999/reject", json={"note": "x"}, headers=as_("grace.lim")).status_code == 404


def test_an_approved_lesson_is_given_to_the_next_case_and_saves_a_review_round(db):
    db.add(m.Lesson(key=Q6_KEY, kind="REVIEW_FINDING", q_id="Q6", agents=["A3_DRAFT"], policy_ref="7.2.4",
                    text="Q6: Disposition must state the screening list name and record ID. (policy 7.2.4)",
                    status="APPROVED", evidence=[{"case_ref": "RA-2025-0001", "source": "review finding C2", "detail": ""}]))
    db.flush()
    row = close_halcyon(db)
    rules = [r for d in db.scalars(select(m.MasterDecision).where(m.MasterDecision.case_id == row.id))
             for r in d.rules_matched]
    assert "R7" not in rules and row.final_rating == "MEDIUM"
    given = [t.inputs["lessons"] for t in db.scalars(select(m.AgentTask).where(m.AgentTask.case_id == row.id))
             if "lessons" in t.inputs]
    lesson = db.scalar(select(m.Lesson).where(m.Lesson.key == Q6_KEY))
    assert given and all(x[0]["id"] == store.lesson_ref(lesson.id) for x in given)
    assert lesson.times_used == len(given)
    assert lesson.status == "APPROVED" and lesson.occurrences == 1  # no review finding this time, so no new evidence
