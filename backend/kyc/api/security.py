"""Passwords and session tokens (HS256 JWTs). No FastAPI routing here, so deps.py and auth.py can both use it."""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..db import models as m

log = logging.getLogger("kyc.auth")
COOKIE = "kyc_session"
ALGORITHM = "HS256"

config.check()
if config.JWT_SECRET:
    SECRET = config.JWT_SECRET
else:
    SECRET = secrets.token_urlsafe(48)
    log.warning("KYC_JWT_SECRET is not set: using a random secret, so sessions end when the API restarts")

# checked when the username does not exist, so a wrong username takes as long as a wrong password
_DUMMY_HASH = bcrypt.hashpw(b"not-a-password", bcrypt.gensalt(rounds=10))


def issue_token(user: m.User, now: datetime | None = None) -> tuple[str, datetime]:
    now = now or datetime.now(timezone.utc)
    exp = now + timedelta(hours=config.TOKEN_HOURS)
    return jwt.encode({"sub": user.username, "iat": now, "exp": exp}, SECRET, algorithm=ALGORITHM), exp


def username_from_token(token: str) -> str:
    """The token's subject; 401 when it is malformed, tampered with or expired."""
    try:
        claims = jwt.decode(token, SECRET, algorithms=[ALGORITHM], options={"require": ["sub", "exp"]})
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired; sign in again", headers={"WWW-Authenticate": "Bearer"})
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid session; sign in again", headers={"WWW-Authenticate": "Bearer"})
    return claims["sub"]


def check_password(db: Session, username: str, password: str) -> m.User | None:
    user = db.scalar(select(m.User).where(m.User.username == username))
    ok = bcrypt.checkpw(password.encode(), user.password_hash.encode() if user else _DUMMY_HASH)
    return user if ok and user and user.is_active else None
