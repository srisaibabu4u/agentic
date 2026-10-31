"""FastAPI application.

    uvicorn kyc.api.app:app --reload        # from backend/; docs at http://localhost:8000/docs

The API reads cases and records maker/checker actions as events. The worker
(python -m kyc.worker) must be running for cases to move.
"""
from __future__ import annotations

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..db import models as m
from . import actions, cases, workflow
from .cases import user_ref
from .deps import current_user, get_db, wall_clock

CORS_ORIGINS = os.getenv("KYC_CORS_ORIGINS", "http://localhost:5173").split(",")


def create_app() -> FastAPI:
    app = FastAPI(title="KYC risk assessment API", version="0.2.0",
                  description="Client risk assessment renewals: master + agents with maker/checker review. Dummy data only. "
                              "Until JWT login, send the acting username in the X-User header.")
    app.state.session_factory = SessionLocal
    app.state.clock = wall_clock
    app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
    app.include_router(cases.router)
    app.include_router(actions.router)
    app.include_router(workflow.router)

    @app.get("/health", tags=["meta"])
    def health(db: Session = Depends(get_db)) -> dict:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get("/users", tags=["meta"])
    def users(db: Session = Depends(get_db)) -> list[dict]:
        """For the UI's user switcher until JWT login (Phase 4); deliberately needs no X-User."""
        return [user_ref(u) | {"role": u.role}
                for u in db.scalars(select(m.User).where(m.User.is_active).order_by(m.User.role, m.User.username))]

    @app.get("/me", tags=["meta"])
    def me(user: m.User = Depends(current_user)) -> dict:
        return user_ref(user) | {"role": user.role}

    return app


app = create_app()
