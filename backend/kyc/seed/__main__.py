"""Load reference data, users and dummy clients into Postgres.

    python -m kyc.seed --clients 25 --reset            # from backend/
    python -m kyc.seed --clients 10 --seed 7 --as-of 2026-11-02

Each client gets its documents written as text files under GENERATED_DIR/<cif>/
and an open renewal case with an unprocessed RENEWAL_DUE event, ready for the
worker (Phase 2). The Halcyon Marine sample case is always included.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
from collections import Counter
from datetime import date

import bcrypt
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..config import GENERATED_DIR
from ..db import SessionLocal
from ..db import models as m
from ..db.base import Base
from .generator import Generator, halcyon
from .reference import COUNTRIES, DUMMY_PASSWORD, EMAIL_DOMAIN, TEAM_LEAD, USERS, country_risk, load_prototype


def _d(v):
    return date.fromisoformat(v) if isinstance(v, str) else v


def reset(db: Session) -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    shutil.rmtree(GENERATED_DIR, ignore_errors=True)


def load_reference(db: Session, proto: dict) -> tuple[m.Checklist, m.Questionnaire]:
    cl = m.Checklist(code=proto["checklist"]["id"], description="Singapore corporate, medium-risk renewal")
    cl.items = [m.ChecklistItem(ordinal=i, item=x["item"], label=x["label"], identity_critical=x["identity_critical"])
                for i, x in enumerate(proto["checklist"]["items"], 1)]
    q = proto["questionnaire"]
    qn = m.Questionnaire(code=q["id"])
    qn.questions = [m.Question(ordinal=i, q_id=x["q_id"], text=x["text"]) for i, x in enumerate(q["questions"], 1)]
    qn.bands = [m.RatingBand(band=b["band"], max_score=b["max"]) for b in q["rating_bands"]]
    db.add_all([cl, qn])
    db.flush()
    for code, lvl in country_risk(proto).items():
        db.add(m.CountryRisk(code=code, name=COUNTRIES.get(code, (code,))[0], risk_level=lvl))
    for ssic, (label, lvl) in q["industry_risk"].items():
        db.add(m.IndustryRisk(ssic_code=ssic, label=label, risk_level=lvl))
    for code, txt in q["policy"].items():
        db.add(m.PolicyRule(code=code, text=txt))
    dep = proto["dependency_map"]
    for item, fields in dep["doc_item_to_fields"].items():
        db.add_all(m.DocItemField(questionnaire_id=qn.id, item=item, field_key=f) for f in fields)
    for field, qs in dep["field_to_questions"].items():
        db.add_all(m.FieldQuestion(questionnaire_id=qn.id, field_key=field, q_id=x) for x in qs)
    return cl, qn


def load_users(db: Session) -> dict[str, m.User]:
    pw = bcrypt.hashpw(DUMMY_PASSWORD.encode(), bcrypt.gensalt(rounds=10)).decode()
    users = {u["username"]: m.User(**u, email=f"{u['username']}@{EMAIL_DOMAIN}", password_hash=pw) for u in USERS}
    db.add_all(users.values())
    db.flush()
    return users


def load_client(db: Session, spec: dict, cl: m.Checklist, qn: m.Questionnaire,
                maker: m.User, checker: m.User, lead: m.User) -> m.Case:
    client = m.Client(**spec["client"])
    db.add(client)
    db.flush()

    by_key: dict[str, m.RelatedParty] = {}
    for p in spec["parties"]:  # parents come before children in every spec
        rp = m.RelatedParty(client_id=client.id, parent_id=by_key[p["parent_key"]].id if p.get("parent_key") else None,
                            party_type=p["party_type"], name=p["name"], nationality=p.get("nationality"),
                            date_of_birth=p.get("dob"), id_document=p.get("id_document"),
                            is_director=p.get("is_director", False), is_shareholder=p.get("is_shareholder", False),
                            is_ubo=p.get("is_ubo", False), ownership_pct=p.get("ownership_pct"),
                            shares_held=p.get("shares_held"), appointed_on=p.get("appointed_on"),
                            source_of_wealth=p.get("source_of_wealth"))
        db.add(rp)
        db.flush()
        by_key[p["key"]] = rp

    folder = GENERATED_DIR / client.cif
    folder.mkdir(parents=True, exist_ok=True)
    for d in spec["documents"]:
        body = d["text"].encode("utf-8")
        path = folder / f"{d['doc_id']}_{d['item']}.txt"
        path.write_bytes(body)
        db.add(m.Document(client_id=client.id, doc_ref=d["doc_id"], checklist_item=d["item"], doc_type=d["type"],
                          source=d["source"], doc_date=_d(d["date"]), fy_end=_d(d.get("fy_end")),
                          expiry=_d(d.get("expiry")), storage_path=path.relative_to(GENERATED_DIR).as_posix(),
                          sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body), text_content=d["text"]))

    c = spec["case"]
    case = m.Case(case_ref=c["case_ref"], client_id=client.id, checklist_id=cl.id, questionnaire_id=qn.id,
                  maker_id=maker.id, checker_id=checker.id, team_lead_id=lead.id, status="NEW",
                  due_date=c["due_date"], opened_at=c["opened_at"], scenario=c["scenario"])
    db.add(case)
    db.flush()
    db.add(m.CaseEvent(case_id=case.id, seq=1, type="RENEWAL_DUE", source="scheduler",
                       payload={"client_id": client.cif, "due_date": c["due_date"].isoformat()},
                       occurred_at=c["opened_at"]))
    return case


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m kyc.seed", description=__doc__.split("\n")[0])
    ap.add_argument("--clients", type=int, default=25, help="generated clients, on top of Halcyon Marine")
    ap.add_argument("--seed", type=int, default=42, help="random seed (same seed, same data)")
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today(), help="reference date, YYYY-MM-DD")
    ap.add_argument("--reset", action="store_true", help="empty every table and the generated folder first")
    ap.add_argument("--if-empty", action="store_true", help="seed only an empty database; otherwise exit quietly")
    args = ap.parse_args(argv)

    proto = load_prototype()
    with SessionLocal.begin() as db:
        if args.reset:
            reset(db)
        elif db.scalar(select(func.count()).select_from(m.User)):
            if args.if_empty:
                print("Database already has data; not seeding.")
                return
            raise SystemExit("Database already has data. Re-run with --reset to replace it.")

        cl, qn = load_reference(db, proto)
        users = load_users(db)
        makers = [u for u in users.values() if u.role == "maker"]
        checkers = [u for u in users.values() if u.role == "checker"]
        lead = users[TEAM_LEAD]

        load_client(db, halcyon(proto), cl, qn, users["priya.nair"], users["daniel.tan"], lead)
        specs = Generator(args.seed, args.as_of).clients(args.clients)
        for i, spec in enumerate(specs):
            load_client(db, spec, cl, qn, makers[i % len(makers)], checkers[(i // len(makers)) % len(checkers)], lead)

    with SessionLocal() as db:
        print(f"Seeded as of {args.as_of} (seed {args.seed}). Files in {GENERATED_DIR}")
        for model in (m.User, m.Client, m.RelatedParty, m.Document, m.Case, m.CaseEvent, m.ChecklistItem,
                      m.Question, m.CountryRisk, m.DocItemField, m.FieldQuestion):
            print(f"  {model.__tablename__:<18} {db.scalar(select(func.count()).select_from(model)):>4}")
        mix = Counter(db.scalars(select(m.Case.scenario)))
        print("  scenarios: " + ", ".join(f"{k} {v}" for k, v in sorted(mix.items())))
        print(f"  login: any of {', '.join(u['username'] for u in USERS)} / password '{DUMMY_PASSWORD}'")


if __name__ == "__main__":
    main()
