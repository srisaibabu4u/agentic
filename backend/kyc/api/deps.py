"""Request dependencies: database session, signed-in user, business clock.

The session factory and clock live on app.state so tests can swap them. The user comes from the session
token: the httpOnly cookie set by /auth/login, or an "Authorization: Bearer" header for API clients.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterator

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as m
from ..services import store
from .security import COOKIE, username_from_token

Clock = Callable[[Session, m.Case], datetime]


def wall_clock(db: Session, row: m.Case) -> datetime:
    return store.local(datetime.now(timezone.utc))


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as db:
        yield db


def get_clock(request: Request) -> Clock:
    return request.app.state.clock


def current_user(authorization: str | None = Header(default=None, description="Bearer <token> (API clients)"),
                 session: str | None = Cookie(default=None, alias=COOKIE, include_in_schema=False),
                 db: Session = Depends(get_db)) -> m.User:
    token = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else session
    if not token:
        raise HTTPException(401, "Not signed in", headers={"WWW-Authenticate": "Bearer"})
    user = db.scalar(select(m.User).where(m.User.username == username_from_token(token)))
    if user is None or not user.is_active:  # deactivated after the token was issued
        raise HTTPException(401, "Account disabled; sign in again", headers={"WWW-Authenticate": "Bearer"})
    return user
