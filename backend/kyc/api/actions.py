"""Maker and checker actions.

Each action checks who is acting and whether the case is waiting on them, then
appends an event; it never changes the case itself. The worker's master step
applies it (orchestrator.state.apply_human) and decides what happens next.
Clearing waiting_on in the same transaction stops a second submit slipping in
before the worker has processed the first.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as m
from ..services import engine, store
from .cases import get_case
from .deps import Clock, current_user, get_clock, get_db
from .schemas import Accepted, CheckerApprove, CheckerReturn, MakerSubmit

router = APIRouter(tags=["actions"])


def _for_action(db: Session, ref: str, user: m.User, role: str) -> m.Case:
    row = get_case(db, ref, lock=True)
    assigned = row.maker if role == "maker" else row.checker
    if user.role != role or user.id != assigned.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"Only the case's {role}, {assigned.full_name} ({assigned.username}), can do this.")
    if row.waiting_on != role:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Case {ref} is {row.status} and waiting on {row.waiting_on or 'the agents'}; "
                            f"a {role} action is not allowed now.")
    return row


def _queue(db: Session, row: m.Case, type_: str, role: str, payload: dict, user: m.User, clock: Clock) -> Accepted:
    ev = engine.add_event(db, row, type_, role, payload, clock(db, row), actor_id=user.id)
    row.waiting_on = None
    db.commit()
    return Accepted(case_ref=row.case_ref, event_seq=ev.seq)


@router.post("/cases/{ref}/maker/submit", status_code=202, response_model=Accepted)
def maker_submit(ref: str, body: MakerSubmit, db: Session = Depends(get_db), user: m.User = Depends(current_user),
                 clock: Clock = Depends(get_clock)) -> Accepted:
    row = _for_action(db, ref, user, "maker")
    cfg = store.load_config(db, row)
    dep = cfg["dependency_map"]
    items = {i["item"] for i in cfg["checklist"]["items"]} | set(dep["doc_item_to_fields"])
    fields = set(db.scalars(select(m.ExtractedField.field_key).where(m.ExtractedField.case_id == row.id)))
    fields |= {f for fs in dep["doc_item_to_fields"].values() for f in fs}

    errors = [f"uploads[{i}].item: unknown checklist item {u.item!r}" for i, u in enumerate(body.uploads) if u.item not in items]
    errors += [f"field_edits: unknown field {k!r}" for k in body.field_edits if k not in fields]
    errors += [f"dispositions: {k!r} is not an open item" for k in body.dispositions if k not in row.open_items]
    if not (body.uploads or body.field_edits or body.dispositions) and row.open_items:
        errors.append(f"Nothing submitted, but {len(row.open_items)} item(s) are open: {', '.join(row.open_items)}")
    if errors:
        raise HTTPException(422, errors)

    taken = set(db.scalars(select(m.Document.doc_ref).where(m.Document.client_id == row.client_id)))
    uploads = []
    for u in body.uploads:
        doc_id = u.doc_id
        if doc_id is None or doc_id in taken:
            n = max(int(t.split("-")[1]) for t in taken | {"DOC-00"}) + 1
            doc_id = f"DOC-{n:02d}"
        taken.add(doc_id)
        d = {"doc_id": doc_id, "source": u.source, "item": u.item, "type": u.type, "date": u.date.isoformat()}
        d |= {k: getattr(u, k).isoformat() for k in ("fy_end", "expiry") if getattr(u, k)}
        uploads.append(d | {"text": u.text})
    payload = {"uploads": uploads, "field_edits": {k: e.model_dump() for k, e in body.field_edits.items()},
               "dispositions": dict(body.dispositions)}
    return _queue(db, row, "MAKER_SUBMIT", "maker", payload, user, clock)


@router.post("/cases/{ref}/checker/return", status_code=202, response_model=Accepted)
def checker_return(ref: str, body: CheckerReturn, db: Session = Depends(get_db), user: m.User = Depends(current_user),
                   clock: Clock = Depends(get_clock)) -> Accepted:
    row = _for_action(db, ref, user, "checker")
    known = {q.q_id for q in db.get(m.Questionnaire, row.questionnaire_id).questions}
    bad = [r.q_id for r in body.returned if r.q_id not in known]
    if bad:
        raise HTTPException(422, [f"returned: unknown question {q!r}" for q in bad])
    return _queue(db, row, "CHECKER_RETURN", "checker", {"returned": [r.model_dump() for r in body.returned]}, user, clock)


@router.post("/cases/{ref}/checker/approve", status_code=202, response_model=Accepted)
def checker_approve(ref: str, body: CheckerApprove, db: Session = Depends(get_db), user: m.User = Depends(current_user),
                    clock: Clock = Depends(get_clock)) -> Accepted:
    row = _for_action(db, ref, user, "checker")
    return _queue(db, row, "CHECKER_APPROVE", "checker", body.model_dump(), user, clock)
