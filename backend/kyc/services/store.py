"""Convert between the database tables and the prototype's in-memory Case.

The master and agents keep working on orchestrator.models.Case, unchanged:

    load_config(db, row) -> cfg dict      same shape as data/sample_case.json, plus approved lessons
    load_case(db, row, cfg) -> Case       rebuild the Case from the tables
    save_case(db, row, case, ...)         write any changes back
    propose_lessons(db, row)              lessons from a closed case, for a team lead to approve

save_case only writes what changed. Answers and ratings get a new version
instead of being overwritten, and review findings are marked current or
superseded, so the history stays visible for the audit view.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from orchestrator import lessons
from orchestrator.models import Case, Task

from ..config import BUSINESS_TZ_HOURS, UPLOADS_DIR
from ..db import models as m

TZ = timezone(timedelta(hours=BUSINESS_TZ_HOURS))
WAITING_ON = {"WITH_MAKER": "maker", "RETURNED": "maker", "WITH_CHECKER": "checker"}
OPEN_TASK = ("PENDING", "RUNNING")


def local(dt: datetime) -> datetime:
    return dt.astimezone(TZ)


def _date(v) -> date | None:
    return date.fromisoformat(v[:10]) if isinstance(v, str) else v


def _float(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


def person(u: m.User) -> dict:
    return {"id": u.username, "name": u.full_name, "role": u.title}


def doc_dict(d: m.Document) -> dict:
    out = {"doc_id": d.doc_ref, "source": d.source, "item": d.checklist_item, "type": d.doc_type,
           "date": d.doc_date.isoformat()}
    if d.fy_end:
        out["fy_end"] = d.fy_end.isoformat()
    if d.expiry:
        out["expiry"] = d.expiry.isoformat()
    out["text"] = d.text_content
    return out


# ============================================================== config
_REFERENCE: dict[tuple[int, int], dict] = {}


def clear_config_cache() -> None:
    """Reference data is cached per process; call this after changing it (or restart the worker)."""
    _REFERENCE.clear()


def load_config(db: Session, row: m.Case) -> dict:
    """The reference data the master and agents read, in the prototype's JSON shape.
    The client's documents and the approved lessons are read every time; the rest is static and cached."""
    key = (row.checklist_id, row.questionnaire_id)
    if key not in _REFERENCE:
        _REFERENCE[key] = _load_reference(db, row)
    store = db.scalars(select(m.Document).where(m.Document.client_id == row.client_id, m.Document.case_id.is_(None))
                       .order_by(m.Document.id))
    return {**_REFERENCE[key], "document_store": [doc_dict(d) for d in store], "lessons": approved_lessons(db)}


def _load_reference(db: Session, row: m.Case) -> dict:
    cl = db.get(m.Checklist, row.checklist_id)
    qn = db.get(m.Questionnaire, row.questionnaire_id)
    dep_docs, dep_fields = {}, {}
    for r in db.scalars(select(m.DocItemField).where(m.DocItemField.questionnaire_id == qn.id).order_by(m.DocItemField.id)):
        dep_docs.setdefault(r.item, []).append(r.field_key)
    for r in db.scalars(select(m.FieldQuestion).where(m.FieldQuestion.questionnaire_id == qn.id).order_by(m.FieldQuestion.id)):
        dep_fields.setdefault(r.field_key, []).append(r.q_id)
    return {
        "checklist": {"id": cl.code, "items": [{"item": i.item, "label": i.label, "identity_critical": i.identity_critical}
                                               for i in cl.items]},
        "questionnaire": {
            "id": qn.code,
            "questions": [{"q_id": q.q_id, "text": q.text} for q in qn.questions],
            "country_risk": {c.code: c.risk_level for c in db.scalars(select(m.CountryRisk))},
            "industry_risk": {i.ssic_code: [i.label, i.risk_level] for i in db.scalars(select(m.IndustryRisk))},
            "rating_bands": [{"band": b.band, "max": b.max_score} for b in qn.bands],
            "policy": {p.code: p.text for p in db.scalars(select(m.PolicyRule))},
        },
        "dependency_map": {"doc_item_to_fields": dep_docs, "field_to_questions": dep_fields},
    }


def config_version(cfg: dict) -> str:
    return f"{cfg['checklist']['id']}/{cfg['questionnaire']['id']}"


# ============================================================== load
def load_case(db: Session, row: m.Case, cfg: dict) -> Case:
    c = row.client
    meta = {"case_id": row.case_ref, "client_id": c.cif, "legal_name": c.legal_name, "short_name": c.short_name,
            "uen": c.uen, "segment": c.segment, "prior_rating": c.prior_rating,
            "prior_assessment_date": c.prior_assessment_date.isoformat() if c.prior_assessment_date else None,
            "due_date": row.due_date.isoformat(), "start_time": local(row.opened_at).isoformat(),
            "maker": person(row.maker), "checker": person(row.checker),
            "team_lead": person(row.team_lead) if row.team_lead else None,
            "case_url": f"https://kyc.example.internal/cases/{row.case_ref}"}
    case = Case(row.case_ref, meta, status=row.status, manifest=row.manifest, open_items=dict(row.open_items),
                retries=dict(row.retries), maker_submitted=row.maker_submitted, task_seq=row.task_seq)

    # documents: what Agent 1 selected (with its freshness verdict), then the maker's uploads
    uploads = list(db.scalars(select(m.Document).where(m.Document.case_id == row.id).order_by(m.Document.id)))
    known = {d["doc_id"]: d for d in cfg["document_store"]} | {u.doc_ref: doc_dict(u) for u in uploads}
    for f in (row.manifest or {}).get("found", []):
        case.docs[f["doc_id"]] = {**known[f["doc_id"]], "fresh": f["fresh"]}
    for u in uploads:
        case.docs[u.doc_ref] = {**doc_dict(u), "fresh": True}

    # extracted fields, and the maker's confirmations of them
    edits = []
    for f in db.scalars(select(m.ExtractedField).where(m.ExtractedField.case_id == row.id).order_by(m.ExtractedField.id)):
        d = {"value": f.value}
        if f.raw is not None:
            d["raw"] = f.raw
        d |= {"confidence": float(f.confidence), "source": f.source_ref}
        if f.conflict is not None:
            d["conflict"] = f.conflict
        d["note"] = f.note
        case.fields[f.field_key] = d
        if f.confirmed_by:
            edits.append(f)
    for f in sorted(edits, key=lambda f: (f.confirmed_at, f.id)):
        case.maker_inputs["edits"][f.field_key] = {"value": f.value, "note": f.confirm_note}
    for d in db.scalars(select(m.Disposition).where(m.Disposition.case_id == row.id).order_by(m.Disposition.id)):
        case.maker_inputs["dispositions"][d.ref] = d.rationale

    # current answers (suggestions are not case state), latest rating, current findings
    for a in db.scalars(select(m.Answer).where(m.Answer.case_id == row.id, m.Answer.is_current).order_by(m.Answer.id)):
        case.answers[a.q_id] = answer_dict(a)
    case.answers = dict(sorted(case.answers.items(), key=lambda kv: _q_order(cfg, kv[0])))
    r = db.scalars(select(m.Rating).where(m.Rating.case_id == row.id).order_by(m.Rating.version.desc())).first()
    case.rating = rating_dict(r) if r else None
    case.findings = [finding_dict(f) for f in db.scalars(
        select(m.ReviewFinding).where(m.ReviewFinding.case_id == row.id, m.ReviewFinding.is_current)
        .order_by(m.ReviewFinding.id))]

    for cr in db.scalars(select(m.CheckerReturn).where(m.CheckerReturn.case_id == row.id).order_by(m.CheckerReturn.id)):
        case.checker_returns.append({"q_id": cr.q_id, "comment": cr.comment} | ({"resolved": True} if cr.resolved else {}))

    if row.final_rating:
        final = db.scalars(select(m.Rating).where(m.Rating.case_id == row.id, m.Rating.is_final)).one()
        case.final = {"rating": row.final_rating, "score": rating_dict(final), "approved_by": row.checker.full_name,
                      "approved_at": local(row.closed_at).isoformat(),
                      "next_review": row.next_review_date.isoformat(), "comment": row.approval_comment}

    case.pending = [Task(t.task_ref, t.agent, t.scope, t.inputs, list(t.depends_on), t.retry) for t in db.scalars(
        select(m.AgentTask).where(m.AgentTask.case_id == row.id, m.AgentTask.status.in_(OPEN_TASK))
        .order_by(m.AgentTask.id))]
    return case


def _q_order(cfg: dict, q_id: str) -> int:
    ids = [q["q_id"] for q in cfg["questionnaire"]["questions"]]
    return ids.index(q_id) if q_id in ids else len(ids)


def answer_dict(a: m.Answer) -> dict:
    d = {"q_id": a.q_id, "answer": a.answer, "score": a.score, "score_reason": a.score_reason,
         "citations": a.citations, "flags": list(a.flags), "confidence": _float(a.confidence)}
    if a.resolves is not None:
        d["resolves"] = a.resolves
    if a.evidence_needed is not None:
        d["evidence_needed"] = a.evidence_needed
    return d


def rating_dict(r: m.Rating) -> dict:
    return {"total": r.total, "max": r.max_score, "working": r.working, "band": r.band}


def finding_dict(f: m.ReviewFinding) -> dict:
    return {"q_id": f.q_id, "check": f.check_code, "severity": f.severity, "excerpt": f.excerpt, "issue": f.issue,
            "policy_ref": f.policy_ref, "evidence_available": f.evidence_available}


# ============================================================== save
def save_case(db: Session, row: m.Case, case: Case, *, task_id: int | None = None,
              decision_id: int | None = None, actor_id: int | None = None) -> None:
    """Write the Case back. task_id / decision_id record which agent task or master decision caused the change."""
    row.status = case.status
    row.manifest = case.manifest
    row.open_items = dict(case.open_items)
    row.retries = dict(case.retries)
    row.maker_submitted = case.maker_submitted
    row.task_seq = case.task_seq

    docs = _save_uploads(db, row, case, actor_id or row.maker_id)
    _save_fields(db, row, case, docs, task_id, actor_id or row.maker_id)
    _save_dispositions(db, row, case, actor_id or row.maker_id)
    _save_answers(db, row, case, task_id)
    _save_rating(db, row, case)
    _save_findings(db, row, case, task_id)
    _save_checker_returns(db, row, case)
    _save_final(db, row, case)
    _save_tasks(db, row, case, decision_id)
    row.waiting_on = WAITING_ON.get(case.status) if not case.pending else None
    db.flush()


def _save_uploads(db, row, case, actor_id) -> dict[str, int]:
    """Documents the maker added during the case. Returns doc_ref -> documents.id for the whole client."""
    docs = {d.doc_ref: d for d in db.scalars(select(m.Document).where(m.Document.client_id == row.client_id))}
    for ref, d in case.docs.items():
        if ref in docs:
            continue
        body = d["text"].encode("utf-8")
        path = UPLOADS_DIR / row.case_ref / f"{ref}_{d['item']}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        doc = m.Document(client_id=row.client_id, case_id=row.id, doc_ref=ref, checklist_item=d["item"],
                         doc_type=d["type"], source=d["source"], doc_date=_date(d["date"]), fy_end=_date(d.get("fy_end")),
                         expiry=_date(d.get("expiry")), storage_path=path.relative_to(UPLOADS_DIR).as_posix(),
                         sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body), text_content=d["text"],
                         uploaded_by=actor_id)
        db.add(doc)
        docs[ref] = doc
    db.flush()
    return {ref: d.id for ref, d in docs.items()}


def _save_fields(db, row, case, docs, task_id, actor_id):
    rows = {f.field_key: f for f in db.scalars(select(m.ExtractedField).where(m.ExtractedField.case_id == row.id))}
    for key, f in case.fields.items():
        r = rows.get(key)
        src = f.get("source") or ""
        doc_ref, _, page = src.partition("#p")
        vals = dict(value=f.get("value"), raw=f.get("raw"), confidence=f.get("confidence"), source_ref=src,
                    document_id=docs.get(doc_ref), source_page=int(page) if page.isdigit() else None,
                    conflict=f.get("conflict"), note=f.get("note"))
        if r is None:
            r = m.ExtractedField(case_id=row.id, field_key=key, task_id=task_id, **vals)
            db.add(r)
        elif any(getattr(r, k) != (Decimal(str(v)) if k == "confidence" else v) for k, v in vals.items()):
            for k, v in vals.items():
                setattr(r, k, v)
            r.task_id, r.updated_at = task_id, func.now()
        edit = case.maker_inputs["edits"].get(key)
        if edit and (r.confirmed_by is None or r.confirm_note != edit.get("note")):
            r.confirmed_by, r.confirmed_at, r.confirm_note = actor_id, func.now(), edit.get("note")
    db.flush()


def _save_dispositions(db, row, case, actor_id):
    have = {d.ref for d in db.scalars(select(m.Disposition).where(m.Disposition.case_id == row.id))}
    for ref, text in case.maker_inputs["dispositions"].items():
        if ref not in have:
            outcome = "FALSE_POSITIVE" if text.lower().startswith("false positive") else "TRUE_MATCH"
            db.add(m.Disposition(case_id=row.id, ref=ref, outcome=outcome, rationale=text, decided_by=actor_id))
    # screening hits as rows, for the UI (the case keeps them in the screening.hits field)
    hits = (case.fields.get("screening.hits") or {}).get("value") or []
    if hits and not db.scalar(select(func.count()).select_from(m.ScreeningHit).where(m.ScreeningHit.case_id == row.id)):
        for h in hits:
            db.add(m.ScreeningHit(case_id=row.id, party_name=h.get("name", "?"), list_name=h.get("list", "?"),
                                  record_id=h.get("record", "?"), listed_name=h.get("listed name"),
                                  listed_dob=_date(h.get("listed dob")), listed_nationality=h.get("listed nationality")))


def _save_answers(db, row, case, task_id):
    current = {a.q_id: a for a in db.scalars(select(m.Answer).where(m.Answer.case_id == row.id, m.Answer.is_current))}
    for q, a in case.answers.items():
        cur = current.get(q)
        if cur is not None and answer_dict(cur) == a:
            continue
        version = (db.scalar(select(func.max(m.Answer.version)).where(m.Answer.case_id == row.id, m.Answer.q_id == q)) or 0) + 1
        if cur is not None:
            cur.is_current = False
            db.flush()  # the partial unique index allows one current answer per question
        db.add(_answer_row(row.id, a, version, "draft" if cur is None else "redraft", True, task_id))
    db.flush()


def _answer_row(case_id, a, version, mode, is_current, task_id) -> m.Answer:
    return m.Answer(case_id=case_id, q_id=a["q_id"], version=version, is_current=is_current, mode=mode,
                    answer=a["answer"], score=a.get("score"), score_reason=a.get("score_reason"),
                    citations=a.get("citations", []), flags=a.get("flags", []), confidence=a.get("confidence"),
                    resolves=a.get("resolves"), evidence_needed=a.get("evidence_needed"), task_id=task_id)


def save_suggestions(db, row, answers: list[dict], task_id):
    """Agent 3 'suggest' output: kept for the maker, never the current answer."""
    for a in answers:
        version = (db.scalar(select(func.max(m.Answer.version)).where(m.Answer.case_id == row.id, m.Answer.q_id == a["q_id"])) or 0) + 1
        db.add(_answer_row(row.id, a, version, "suggest", False, task_id))


def _latest_rating(db, row):
    return db.scalars(select(m.Rating).where(m.Rating.case_id == row.id).order_by(m.Rating.version.desc())).first()


def _save_rating(db, row, case):
    if not case.rating:
        return
    last = _latest_rating(db, row)
    if last is None or rating_dict(last) != case.rating:
        db.add(m.Rating(case_id=row.id, version=(last.version if last else 0) + 1, total=case.rating["total"],
                        max_score=case.rating["max"], working=case.rating["working"], band=case.rating["band"]))
        db.flush()


def _save_findings(db, row, case, task_id):
    cur = list(db.scalars(select(m.ReviewFinding).where(m.ReviewFinding.case_id == row.id, m.ReviewFinding.is_current)
                          .order_by(m.ReviewFinding.id)))
    if [finding_dict(f) for f in cur] == case.findings and (task_id is None or all(f.task_id == task_id for f in cur)):
        return
    db.execute(update(m.ReviewFinding).where(m.ReviewFinding.case_id == row.id).values(is_current=False))
    for f in case.findings:
        db.add(m.ReviewFinding(case_id=row.id, task_id=task_id, q_id=f["q_id"], check_code=f["check"],
                               severity=f["severity"], excerpt=f.get("excerpt"), issue=f["issue"],
                               policy_ref=f.get("policy_ref"), evidence_available=f.get("evidence_available", False)))


def _save_checker_returns(db, row, case):
    rows = list(db.scalars(select(m.CheckerReturn).where(m.CheckerReturn.case_id == row.id).order_by(m.CheckerReturn.id)))
    for i, r in enumerate(case.checker_returns):
        if i < len(rows):
            if r.get("resolved") and not rows[i].resolved:
                rows[i].resolved, rows[i].resolved_at = True, func.now()
        else:
            db.add(m.CheckerReturn(case_id=row.id, q_id=r["q_id"], comment=r["comment"], returned_by=row.checker_id,
                                   resolved=bool(r.get("resolved"))))


def _save_final(db, row, case):
    if not case.final or row.final_rating:
        return
    f = case.final
    s = f["score"]
    last = _latest_rating(db, row)
    db.add(m.Rating(case_id=row.id, version=(last.version if last else 0) + 1, total=s["total"], max_score=s["max"],
                    working=s["working"], band=s["band"], is_final=True, approved_by=row.checker_id))
    row.final_rating, row.approval_comment = f["rating"], f.get("comment")
    row.next_review_date, row.closed_at = _date(f["next_review"]), datetime.fromisoformat(f["approved_at"])
    row.client.next_review_date = row.next_review_date


def _save_tasks(db, row, case, decision_id):
    have = set(db.scalars(select(m.AgentTask.task_ref).where(m.AgentTask.case_id == row.id)))
    for t in case.pending:
        if t.task_id not in have:
            db.add(m.AgentTask(task_ref=t.task_id, case_id=row.id, decision_id=decision_id, agent=t.agent,
                               scope=t.scope, inputs=t.inputs, depends_on=t.depends_on, retry=t.retry))


# ============================================================== lessons
def lesson_ref(lesson_id: int) -> str:
    return f"L-{lesson_id:04d}"


def lesson_dict(x: m.Lesson) -> dict:
    """What an agent is given: no evidence or review details."""
    return {"id": lesson_ref(x.id), "q_id": x.q_id, "agents": list(x.agents), "text": x.text,
            "occurrences": x.occurrences, "kind": x.kind, "policy_ref": x.policy_ref}


def approved_lessons(db: Session) -> list[dict]:
    return [lesson_dict(x) for x in db.scalars(select(m.Lesson).where(m.Lesson.status == "APPROVED").order_by(m.Lesson.id))]


def propose_lessons(db: Session, row: m.Case) -> list[m.Lesson]:
    """Turn a closed case's review findings (every round, not only the last) and checker returns into lessons.
    A problem already known adds this case as evidence; a new one is proposed for a team lead to approve.
    Rejected and retired lessons keep counting evidence but are not proposed again."""
    findings = [finding_dict(f) for f in db.scalars(
        select(m.ReviewFinding).where(m.ReviewFinding.case_id == row.id).order_by(m.ReviewFinding.id))]
    returns = [{"q_id": r.q_id, "comment": r.comment} for r in db.scalars(
        select(m.CheckerReturn).where(m.CheckerReturn.case_id == row.id).order_by(m.CheckerReturn.id))]
    touched = []
    for c in lessons.candidates(row.case_ref, findings, returns):
        x = db.scalar(select(m.Lesson).where(m.Lesson.key == c["key"]).with_for_update())
        if x is None:
            x = m.Lesson(key=c["key"], kind=c["kind"], q_id=c["q_id"], agents=c["agents"], policy_ref=c["policy_ref"],
                         text=c["text"], evidence=c["evidence"])
            db.add(x)
        elif row.case_ref not in {e["case_ref"] for e in x.evidence}:
            x.evidence = [*x.evidence, *c["evidence"]]
            x.occurrences += 1
        touched.append(x)
    db.flush()
    return touched


def mark_lessons_used(db: Session, refs: list[str]) -> None:
    ids = [int(r.split("-")[1]) for r in refs]
    if ids:
        db.execute(update(m.Lesson).where(m.Lesson.id.in_(ids)).values(times_used=m.Lesson.times_used + 1))
