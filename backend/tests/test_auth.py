"""Login, sessions and logout."""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from kyc import config
from kyc.api.app import create_app
from kyc.api.security import COOKIE, issue_token
from kyc.db import models as m
from kyc.seed.reference import DUMMY_PASSWORD


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(config, "COOKIE_SECURE", False)  # the test client talks plain http
    app = create_app()
    app.state.session_factory = lambda: contextlib.nullcontext(db)
    return TestClient(app)


def login(client, username, password=DUMMY_PASSWORD):
    return client.post("/auth/login", json={"username": username, "password": password})


def test_wrong_password_and_unknown_user_look_the_same(client):
    a, b = login(client, "priya.nair", "wrong"), login(client, "no.such.user", "wrong")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"detail": "Wrong username or password"}
    assert COOKIE not in client.cookies


def test_login_sets_an_httponly_cookie_that_signs_the_browser_in(client):
    r = login(client, "priya.nair")
    assert r.status_code == 200 and r.json()["user"]["role"] == "maker"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/me").json()["username"] == "priya.nair"  # cookie only, no header
    assert client.get("/cases").status_code == 200


def test_bearer_token_for_api_clients(client):
    token = login(client, "daniel.tan").json()["access_token"]
    client.cookies.clear()
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["role"] == "checker"


def test_logout_ends_the_session(client):
    login(client, "priya.nair")
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/me").status_code == 401


def test_tampered_and_expired_tokens_are_refused(client):
    token = issue_token(SimpleNamespace(username="priya.nair"))[0]
    head, body, sig = token.split(".")
    forged = f"{head}.{body}.{sig[:-2]}{'AA' if sig[-2:] != 'AA' else 'BB'}"
    assert client.get("/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    old = issue_token(SimpleNamespace(username="priya.nair"), now=datetime.now(timezone.utc) - timedelta(days=2))[0]
    r = client.get("/me", headers={"Authorization": f"Bearer {old}"})
    assert r.status_code == 401 and "expired" in r.json()["detail"]


def test_deactivated_user_is_signed_out_immediately(client, db):
    token = login(client, "aaron.koh").json()["access_token"]
    db.scalar(select(m.User).where(m.User.username == "aaron.koh")).is_active = False
    db.flush()
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert login(client, "aaron.koh").status_code == 401
