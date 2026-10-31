"""Request dependencies: database session, acting user, business clock.

The session factory and clock live on app.state so tests can swap them.
Until JWT login (Phase 4), the acting user comes from the X-User header.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterator

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as m
from ..services import store

Clock = Callable[[Session, m.Case], datetime]


def wall_clock(db: Session, row: m.Case) -> datetime:
    return store.local(datetime.now(timezone.utc))


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as db:
        yield db


def get_clock(request: Request) -> Clock:
    return request.app.state.clock


def current_user(x_user: str | None = Header(default=None, description="Acting username (until JWT login)"),
                 db: Session = Depends(get_db)) -> m.User:
    if not x_user:
        raise HTTPException(401, "Missing X-User header")
    user = db.scalar(select(m.User).where(m.User.username == x_user, m.User.is_active))
    if user is None:
        raise HTTPException(401, f"Unknown or inactive user {x_user!r}")
    return user
