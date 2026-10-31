"""FastAPI application.

    uvicorn kyc.api.app:app --reload        # from backend/; docs at http://localhost:8000/docs

The API reads cases and records maker/checker actions as events. The worker
(python -m kyc.worker) must be running for cases to move.
"""
from __future__ import annotations

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..db import models as m
from . import actions, auth, cases, workflow
from .deps import current_user, get_db, wall_clock

CORS_ORIGINS = [o for o in os.getenv("KYC_CORS_ORIGINS", "").split(",") if o]  # the UI is same-origin via its proxy


def create_app() -> FastAPI:
    app = FastAPI(title="KYC risk assessment API", version="0.4.0",
                  description="Client risk assessment renewals: master + agents with maker/checker review. Dummy data only. "
                              "Sign in with POST /auth/login; browsers get a session cookie, API clients send "
                              "'Authorization: Bearer <access_token>'.")
    app.state.session_factory = SessionLocal
    app.state.clock = wall_clock
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
