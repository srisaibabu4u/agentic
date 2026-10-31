"""Login and logout.

POST /auth/login checks the password and issues a signed JWT, set as an httpOnly, SameSite=Strict cookie
(scripts cannot read it, other sites cannot make the browser send it) and also returned in the body for
API clients, which send it as "Authorization: Bearer <token>". A wrong username and a wrong password get
the same answer in the same time.

Failed attempts are recorded in login_failures. After LOGIN_MAX_FAILURES failures for one username within
LOGIN_WINDOW_MINUTES, that username is refused with 429 until the window passes, even with the right
password. The limit lives in the database, so it holds across every API replica.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import config
from ..db import models as m
from .deps import get_db
from .security import COOKIE, check_password, issue_token, log

router = APIRouter(prefix="/auth", tags=["auth"])


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def public_user(u: m.User) -> dict:
    return {"username": u.username, "full_name": u.full_name, "title": u.title, "role": u.role}


def _locked_for(db: Session, username: str, now: datetime) -> float:
    """Seconds until this username may try again; 0 when it is not locked."""
    window = timedelta(minutes=config.LOGIN_WINDOW_MINUTES)
    n, oldest = db.execute(select(func.count(), func.min(m.LoginFailure.at))
                           .where(m.LoginFailure.username == username, m.LoginFailure.at > now - window)).one()
    return max(0.0, (oldest + window - now).total_seconds()) if n >= config.LOGIN_MAX_FAILURES else 0.0


@router.post("/login")
def login(body: Login, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    username = body.username.strip().lower()
    now = datetime.now(timezone.utc)
    wait = _locked_for(db, username, now)
    if wait:
        minutes = math.ceil(wait / 60)
        log.warning("login locked for %r (%d s left)", username, wait)
        raise HTTPException(429, f"Too many failed sign-ins for this account. Try again in {minutes} minute(s).",
                            headers={"Retry-After": str(math.ceil(wait))})

    user = check_password(db, username, body.password)
    if user is None:
        db.add(m.LoginFailure(username=username, client_ip=request.client.host if request.client else None))
        db.commit()
        log.info("login failed for %r", username)
        raise HTTPException(401, "Wrong username or password")

    db.execute(delete(m.LoginFailure).where(m.LoginFailure.username == username))
    db.commit()
    token, exp = issue_token(user)
    response.set_cookie(COOKIE, token, max_age=int(config.TOKEN_HOURS * 3600), path="/",
                        httponly=True, samesite="strict", secure=config.COOKIE_SECURE)
    log.info("login %s", user.username)
    return {"access_token": token, "token_type": "bearer", "expires_at": exp, "user": public_user(user)}


@router.post("/logout", status_code=204)
def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict", secure=config.COOKIE_SECURE)
    return response
