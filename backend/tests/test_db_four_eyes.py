"""Four-eyes control enforced by Postgres itself. Needs the seeded database
(see the db fixture in conftest.py); every test is rolled back."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from kyc.db import models as m


def user(db, username):
    return db.scalar(select(m.User).where(m.User.username == username))


def halcyon(db):
    return db.scalar(select(m.Case).where(m.Case.case_ref == "RA-2026-0412"))


def test_maker_cannot_be_checker(db):
    case = halcyon(db)
    case.checker_id = case.maker_id
    # the role trigger fires before the ck_cases_four_eyes CHECK; either one must block it
    with pytest.raises(DBAPIError, match="four[-_]eyes"):
        db.flush()


def test_checker_must_have_checker_role(db):
    case = halcyon(db)
    case.checker_id = user(db, "aaron.koh").id  # a maker, and not the case's maker
    with pytest.raises(DBAPIError, match="must have role checker"):
        db.flush()


def test_maker_cannot_approve_final_rating(db):
    case = halcyon(db)
    db.add(m.Rating(case_id=case.id, version=1, total=18, max_score=40, working="2+3+3+2+2+1+2+2",
                    band="MEDIUM", is_final=True, approved_by=case.maker_id))
    with pytest.raises(DBAPIError, match="cannot approve"):
        db.flush()


def test_other_checker_cannot_approve(db):
    case = halcyon(db)
    db.add(m.Rating(case_id=case.id, version=1, total=18, max_score=40, working="x", band="MEDIUM",
                    is_final=True, approved_by=user(db, "siti.rahman").id))
    with pytest.raises(DBAPIError, match="only the assigned checker"):
        db.flush()


def test_assigned_checker_can_approve(db):
    case = halcyon(db)
    db.add(m.Rating(case_id=case.id, version=1, total=18, max_score=40, working="x", band="MEDIUM",
                    is_final=True, approved_by=case.checker_id))
    db.flush()


def test_final_rating_needs_approver(db):
    case = halcyon(db)
    db.add(m.Rating(case_id=case.id, version=1, total=18, max_score=40, working="x", band="MEDIUM", is_final=True))
    with pytest.raises(DBAPIError, match="final_needs_approver"):
        db.flush()
