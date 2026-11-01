"""Lessons from past cases: the team lead's review queue.

The system proposes lessons when a case closes (services.store.propose_lessons).
Anyone signed in can read them; only an admin (team lead) can approve, edit,
reject or retire one. Only approved lessons are given to the agents, and only
from the next decision on: nothing already dispatched changes.

    PROPOSED --approve--> APPROVED --retire--> RETIRED
    PROPOSED --reject---> REJECTED
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as m
from ..services import store
from .deps import current_user, get_db

router = APIRouter(tags=["lessons"], dependencies=[Depends(current_user)])

MOVES = {"approve": ("PROPOSED", "APPROVED"), "reject": ("PROPOSED", "REJECTED"), "retire": ("APPROVED", "RETIRED")}


class Review(BaseModel):
    text: str | None = Field(None, min_length=10, max_length=1000,
                             description="approve only: the lesson as agents will read it, if you want to reword it")
    note: str | None = Field(None, max_length=1000, description="why; required to reject or retire")


def lesson_out(x: m.Lesson) -> dict:
    return {**store.lesson_dict(x), "status": x.status, "evidence": x.evidence, "times_used": x.times_used,
            "proposed_at": x.proposed_at, "reviewed_by": x.reviewer.full_name if x.reviewer else None,
            "reviewed_at": x.reviewed_at, "review_note": x.review_note}


@router.get("/lessons")
def list_lessons(status_: Literal["PROPOSED", "APPROVED", "REJECTED", "RETIRED"] | None = Query(None, alias="status"),
                 db: Session = Depends(get_db)) -> list[dict]:
    q = select(m.Lesson).order_by(m.Lesson.id)
    if status_:
        q = q.where(m.Lesson.status == status_)
    return [lesson_out(x) for x in db.scalars(q)]


@router.post("/lessons/{ref}/{action}")
def review_lesson(ref: str, action: Literal["approve", "reject", "retire"], body: Review,
                  db: Session = Depends(get_db), user: m.User = Depends(current_user)) -> dict:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a team lead (admin) can review lessons.")
    try:
        x = db.get(m.Lesson, int(ref.removeprefix("L-")), with_for_update=True)
    except ValueError:
        x = None
    if x is None:
        raise HTTPException(404, f"No lesson {ref}")
    before, after = MOVES[action]
    if x.status != before:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{ref} is {x.status}; only a {before} lesson can be {action}d.")
    if action != "approve" and not (body.note or "").strip():
        raise HTTPException(422, [f"note: say why you {action} it"])
    if body.text and action == "approve":
        x.text = body.text.strip()
    x.status, x.reviewed_by, x.reviewed_at, x.review_note = after, user.id, datetime.now(timezone.utc), body.note
    db.commit()
    db.refresh(x)
    return lesson_out(x)
