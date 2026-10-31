"""FastAPI application.

    uvicorn kyc.api.app:app --reload        # from backend/; docs at http://localhost:8000/docs

The API reads cases and records maker/checker actions as events. The worker
(python -m kyc.worker) must be running for cases to move.
"""
from __future__ import annotations

import logging
import os
import re
import time
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import logs
from ..db import SessionLocal
from ..db import models as m
from . import actions, auth, cases, workflow
from .deps import current_user, get_db, wall_clock

CORS_ORIGINS = [o for o in os.getenv("KYC_CORS_ORIGINS", "").split(",") if o]  # the UI is same-origin via its proxy
log = logging.getLogger("kyc.api")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def create_app() -> FastAPI:
    logs.setup("api")
    app = FastAPI(title="KYC risk assessment API", version="0.4.0",
                  description="Client risk assessment renewals: master + agents with maker/checker review. Dummy data only. "
                              "Sign in with POST /auth/login; browsers get a session cookie, API clients send "
                              "'Authorization: Bearer <access_token>'.")
    app.state.session_factory = SessionLocal
    app.state.clock = wall_clock

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        """A request id (the caller's X-Request-ID if sane, else a new one) on every log line and in the
        response, and one access line per request: method, path, status, duration, user."""
        given = request.headers.get("x-request-id", "")
        rid = given if _SAFE_ID.match(given) else uuid.uuid4().hex[:16]
        token = logs.request_id.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            log.log(logging.WARNING if response.status_code >= 500 else logging.INFO, "%s %s %s",
                    request.method, request.url.path, response.status_code,
                    extra={"status": response.status_code, "ms": round((time.perf_counter() - start) * 1000, 1),
                           "user": getattr(request.state, "username", None)})
            return response
        except Exception:
            log.exception("unhandled error", extra={"method": request.method, "path": request.url.path})
            raise
        finally:
            logs.request_id.reset(token)

    if CORS_ORIGINS:
        app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
    app.include_router(auth.router)
    app.include_router(cases.router)
    app.include_router(actions.router)
    app.include_router(workflow.router)

    @app.get("/health", tags=["meta"])
    def health(db: Session = Depends(get_db)) -> dict:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get("/me", tags=["meta"])
    def me(user: m.User = Depends(current_user)) -> dict:
        return auth.public_user(user)

    return app


app = create_app()
