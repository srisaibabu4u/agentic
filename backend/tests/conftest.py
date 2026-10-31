from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

import kyc  # noqa: F401  (puts the repo root on sys.path for the orchestrator package)
from kyc.db import engine
from kyc.db import models as m
from kyc.db.session import SessionLocal


@pytest.fixture
def db():
    """A session on the seeded database inside a transaction that is always rolled back.
    Skips when Postgres is not running or not seeded."""
    try:
        conn = engine.connect()
    except OperationalError:
        pytest.skip("Postgres not running")
    tx = conn.begin()
    session = SessionLocal(bind=conn, join_transaction_mode="create_savepoint")
    if not session.scalar(select(m.Case).where(m.Case.case_ref == "RA-2026-0412")):
        pytest.skip("database not seeded")
    yield session
    session.close()
    tx.rollback()
    conn.close()
