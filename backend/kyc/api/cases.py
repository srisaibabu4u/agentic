"""Read endpoints: case list, case detail, timeline, answer history, documents, live stream."""
from __future__ import annotations

import json
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from orchestrator.master import business_days_between

from ..db import models as m
from ..services import store
from .deps import Clock, current_user, get_clock, get_db

router = APIRouter(tags=["cases"], dependencies=[Depends(current_user)])


def get_case(db: Session, ref: str, lock: bool = False) -> m.Case:
    q = select(m.Case).where(m.Case.case_ref == ref)
    row = db.scalar(q.with_for_update() if lock else q)
    if row is None:
        raise HTTPException(404, f"No case {ref}")
    return row


def user_ref(u: m.User | None) -> dict | None:
    return {"username": u.username, "full_name": u.full_name, "title": u.title} if u else None


def _days_left(clock: Clock, db: Session, row: m.Case) -> int:
    return business_days_between(clock(db, row), row.due_date.isoformat())


# ============================================================== list
@router.get("/cases")
def list_cases(db: Session = Depends(get_db), user: m.User = Depends(current_user), clock: Clock = Depends(get_clock),
               status: list[str] | None = Query(None), assigned: bool = Query(False, description="only my cases"),
               waiting_on_me: bool = False, q: str | None = Query(None, description="search case ref or client name"),
               limit: int = Query(100, le=500), offset: int = 0) -> dict:
    stmt = select(m.Case).join(m.Client)
    if status:
        stmt = stmt.where(m.Case.status.in_(status))
    if assigned or waiting_on_me:
        stmt = stmt.where(or_(m.Case.maker_id == user.id, m.Case.checker_id == user.id, m.Case.team_lead_id == user.id))
    if waiting_on_me:
        stmt = stmt.where(or_((m.Case.maker_id == user.id) & (m.Case.waiting_on == "maker"),
                              (m.Case.checker_id == user.id) & (m.Case.waiting_on == "checker")))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(m.Case.case_ref.ilike(like), m.Client.legal_name.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(db.scalars(stmt.order_by(m.Case.due_date, m.Case.id).limit(limit).offset(offset)))

    bands = dict(db.execute(select(m.Rating.case_id, m.Rating.band).ext(distinct_on(m.Rating.case_id))
                            .where(m.Rating.case_id.in_([r.id for r in rows]))
                            .order_by(m.Rating.case_id, m.Rating.version.desc())).all())
    items = []
    for r in rows:
        left = _days_left(clock, db, r)
        items.append({"case_ref": r.case_ref, "status": r.status, "waiting_on": r.waiting_on,
                      "client": {"cif": r.client.cif, "legal_name": r.client.legal_name, "short_name": r.client.short_name},
                      "due_date": r.due_date, "business_days_left": left,
                      "overdue": r.status != "CLOSED" and r.due_date < clock(db, r).date(),
                      "maker": user_ref(r.maker), "checker": user_ref(r.checker), "rating": bands.get(r.id),
                      "final_rating": r.final_rating, "open_items": len(r.open_items or {}), "scenario": r.scenario,
                      "updated_at": r.updated_at})
    return {"total": total, "items": items}


# ============================================================== detail
@router.get("/cases/{ref}")
def case_detail(ref: str, db: Session = Depends(get_db), clock: Clock = Depends(get_clock)) -> dict:
    r = get_case(db, ref)
    c = r.client
    questions = {q.q_id: q.text for q in db.get(m.Questionnaire, r.questionnaire_id).questions}
    manifest = r.manifest or {}
    found = {f["doc_id"]: f for f in manifest.get("found", [])}

    docs = db.scalars(select(m.Document).where(m.Document.client_id == c.id).order_by(m.Document.id))
    documents = [{"id": d.id, "doc_ref": d.doc_ref, "item": d.checklist_item, "type": d.doc_type, "source": d.source,
                  "date": d.doc_date, "fy_end": d.fy_end, "expiry": d.expiry, "size_bytes": d.size_bytes,
                  "uploaded_by": user_ref(db.get(m.User, d.uploaded_by)) if d.uploaded_by else None,
                  "selected": d.doc_ref in found or d.case_id == r.id,
                  "fresh": found[d.doc_ref]["fresh"] if d.doc_ref in found else (True if d.case_id == r.id else None)}
                 for d in docs]

    fields = [{"key": f.field_key, "value": f.value, "confidence": f.confidence, "source": f.source_ref,
               "document_id": f.document_id, "page": f.source_page, "note": f.note, "low_confidence": f.confidence < 0.8,
               "confirmed_by": user_ref(db.get(m.User, f.confirmed_by)) if f.confirmed_by else None,
               "confirmed_at": f.confirmed_at, "confirm_note": f.confirm_note}
              for f in db.scalars(select(m.ExtractedField).where(m.ExtractedField.case_id == r.id).order_by(m.ExtractedField.id))]

    answers, suggestions = [], {}
    for a in db.scalars(select(m.Answer).where(m.Answer.case_id == r.id).order_by(m.Answer.q_id, m.Answer.version)):
        if a.mode == "suggest":
            suggestions[a.q_id] = {**store.answer_dict(a), "version": a.version, "created_at": a.created_at}
        elif a.is_current:
            answers.append({**store.answer_dict(a), "question": questions.get(a.q_id), "version": a.version,
                            "mode": a.mode, "updated_at": a.created_at})
    answers.sort(key=lambda a: list(questions).index(a["q_id"]) if a["q_id"] in questions else 99)

    ratings = list(db.scalars(select(m.Rating).where(m.Rating.case_id == r.id).order_by(m.Rating.version)))
    final = next((x for x in ratings if x.is_final), None)
    users = {u.id: u for u in db.scalars(select(m.User))}
    return {
        "case_ref": r.case_ref, "status": r.status, "waiting_on": r.waiting_on, "scenario": r.scenario,
        "due_date": r.due_date, "business_days_left": _days_left(clock, db, r), "opened_at": r.opened_at,
        "closed_at": r.closed_at, "next_review_date": r.next_review_date, "version": r.version,
        "maker": user_ref(r.maker), "checker": user_ref(r.checker), "team_lead": user_ref(r.team_lead),
        "config": {"checklist": db.get(m.Checklist, r.checklist_id).code,
                   "questionnaire": db.get(m.Questionnaire, r.questionnaire_id).code},
        "client": {"cif": c.cif, "legal_name": c.legal_name, "short_name": c.short_name, "uen": c.uen,
                   "segment": c.segment, "principal_activity": c.principal_activity, "ssic_code": c.ssic_code,
                   "prior_rating": c.prior_rating, "prior_assessment_date": c.prior_assessment_date,
                   "parties": [{"id": p.id, "parent_id": p.parent_id, "type": p.party_type, "name": p.name,
                                "nationality": p.nationality, "director": p.is_director, "shareholder": p.is_shareholder,
                                "ubo": p.is_ubo, "ownership_pct": p.ownership_pct} for p in c.parties]},
        "documents": documents, "missing_documents": manifest.get("missing", []),
        "fields": fields, "open_items": r.open_items,
        "answers": answers, "suggestions": list(suggestions.values()),
        "rating": store.rating_dict(ratings[-1]) if ratings else None,
        "final": {"rating": r.final_rating, "score": store.rating_dict(final), "approved_by": user_ref(users.get(final.approved_by)),
                  "comment": r.approval_comment} if final else None,
        "findings": [store.finding_dict(f) for f in db.scalars(
            select(m.ReviewFinding).where(m.ReviewFinding.case_id == r.id, m.ReviewFinding.is_current))],
        "checker_returns": [{"q_id": x.q_id, "comment": x.comment, "returned_by": user_ref(users[x.returned_by]),
                             "returned_at": x.returned_at, "resolved": x.resolved}
                            for x in db.scalars(select(m.CheckerReturn).where(m.CheckerReturn.case_id == r.id).order_by(m.CheckerReturn.id))],
        "screening": {
            "hits": [{"party": h.party_name, "list": h.list_name, "record": h.record_id, "listed_name": h.listed_name,
                      "listed_dob": h.listed_dob, "listed_nationality": h.listed_nationality}
                     for h in db.scalars(select(m.ScreeningHit).where(m.ScreeningHit.case_id == r.id))],
            "dispositions": [{"ref": d.ref, "outcome": d.outcome, "rationale": d.rationale,
                              "decided_by": user_ref(users[d.decided_by]), "decided_at": d.decided_at}
                             for d in db.scalars(select(m.Disposition).where(m.Disposition.case_id == r.id))]},
        "open_tasks": [_task(t) for t in db.scalars(select(m.AgentTask).where(
            m.AgentTask.case_id == r.id, m.AgentTask.status.in_(store.OPEN_TASK)).order_by(m.AgentTask.id))],
        "notifications": [{"to": user_ref(users[n.to_user_id]), "cc": [user_ref(users[i]) for i in n.cc_user_ids],
                           "template": n.template, "subject": n.subject, "body": n.body, "sent_at": n.sent_at}
                          for n in db.scalars(select(m.Notification).where(m.Notification.case_id == r.id).order_by(m.Notification.id))],
    }


def _task(t: m.AgentTask) -> dict:
    return {"task_ref": t.task_ref, "agent": t.agent, "status": t.status, "scope": t.scope, "depends_on": list(t.depends_on),
            "retry": t.retry, "live": t.live, "claimed_by": t.claimed_by, "claimed_at": t.claimed_at,
            "finished_at": t.finished_at, "error": t.error}


# ============================================================== timeline
@router.get("/cases/{ref}/timeline")
def timeline(ref: str, db: Session = Depends(get_db), full: bool = Query(False, description="include agent result payloads")) -> dict:
    """Every event in order, each with the master decision it led to and the tasks that decision dispatched."""
    r = get_case(db, ref)
    users = {u.id: u for u in db.scalars(select(m.User))}
    tasks = {t.id: t for t in db.scalars(select(m.AgentTask).where(m.AgentTask.case_id == r.id))}
    decisions = {d.event_id: d for d in db.scalars(select(m.MasterDecision).where(m.MasterDecision.case_id == r.id))}
    out = []
    for ev in db.scalars(select(m.CaseEvent).where(m.CaseEvent.case_id == r.id).order_by(m.CaseEvent.seq)):
        t = tasks.get(ev.task_id)
        entry = {"seq": ev.seq, "type": ev.type, "source": ev.source, "occurred_at": ev.occurred_at,
                 "actor": user_ref(users.get(ev.actor_id)), "processed": ev.processed_at is not None,
                 "task": _task(t) if t else None}
        if ev.type != "AGENT_RESULT" or full:
            entry["payload"] = ev.payload
        d = decisions.get(ev.id)
        if d:
            entry["decision"] = {"decision": d.decision, "prev_status": d.prev_status, "new_status": d.new_status,
                                 "rules_matched": d.rules_matched, "guardrails_triggered": d.guardrails_triggered,
                                 "rationale": d.rationale, "decided_at": d.decided_at, "config_version": d.config_version,
                                 "state": d.state_snapshot,
                                 "tasks": [_task(x) for x in sorted(tasks.values(), key=lambda x: x.id) if x.decision_id == d.id]}
        out.append(entry)
    return {"case_ref": r.case_ref, "status": r.status, "events": out}


@router.get("/cases/{ref}/answers/{q_id}")
def answer_history(ref: str, q_id: str, db: Session = Depends(get_db)) -> dict:
    r = get_case(db, ref)
    rows = list(db.scalars(select(m.Answer).where(m.Answer.case_id == r.id, m.Answer.q_id == q_id).order_by(m.Answer.version)))
    if not rows:
        raise HTTPException(404, f"No answers for {q_id} on {ref}")
    return {"case_ref": ref, "q_id": q_id, "versions": [
        {**store.answer_dict(a), "version": a.version, "mode": a.mode, "is_current": a.is_current,
         "task_ref": db.get(m.AgentTask, a.task_id).task_ref if a.task_id else None, "created_at": a.created_at}
        for a in rows]}


@router.get("/documents/{doc_id}")
def document(doc_id: int, db: Session = Depends(get_db)) -> dict:
    d = db.get(m.Document, doc_id)
    if d is None:
        raise HTTPException(404, f"No document {doc_id}")
    return {**store.doc_dict(d), "id": d.id, "client_cif": db.get(m.Client, d.client_id).cif, "sha256": d.sha256}


# ============================================================== live updates
@router.get("/cases/{ref}/stream")
def stream(ref: str, request: Request, timeout: float = Query(300, le=3600, description="seconds before the stream ends")):
    """Server-sent events: one 'case' event whenever the case changes. Ends when the case closes or on timeout;
    browsers' EventSource reconnects automatically."""
    factory = request.app.state.session_factory
    with factory() as db:
        get_case(db, ref)

    def events():
        last, deadline = None, time.monotonic() + timeout
        while time.monotonic() < deadline:
            with factory() as db:
                r = get_case(db, ref)
                seq = db.scalar(select(func.max(m.CaseEvent.seq)).where(m.CaseEvent.case_id == r.id))
                undecided = db.scalar(select(func.count()).select_from(m.CaseEvent).where(
                    m.CaseEvent.case_id == r.id, m.CaseEvent.processed_at.is_(None)))
                running = db.scalar(select(func.count()).select_from(m.AgentTask).where(
                    m.AgentTask.case_id == r.id, m.AgentTask.status == "RUNNING"))
                now = {"case_ref": ref, "status": r.status, "waiting_on": r.waiting_on, "version": r.version,
                       "last_seq": seq, "undecided_events": undecided, "running_tasks": running}
            if now != last:
                yield f"event: case\ndata: {json.dumps(now)}\n\n"
                last = now
            if now["status"] == "CLOSED":
                return
            time.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
