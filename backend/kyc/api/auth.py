"""Login and logout.

POST /auth/login checks the password and issues a signed JWT, set as an httpOnly, SameSite=Strict cookie
(scripts cannot read it, other sites cannot make the browser send it) and also returned in the body for
API clients, which send it as "Authorization: Bearer <token>". A wrong username and a wrong password get
the same answer in the same time.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
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


@router.post("/login")
def login(body: Login, response: Response, db: Session = Depends(get_db)) -> dict:
    user = check_password(db, body.username.strip(), body.password)
    if user is None:
        log.info("login failed for %r", body.username)
        raise HTTPException(401, "Wrong username or password")
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
