"""The API end to end: Halcyon driven over HTTP by its maker and checker, with the worker
moving the case between actions. Also: who may act, when, and with what."""
from __future__ import annotations

import contextlib
import io
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from orchestrator.engine import Engine

from kyc.api.app import create_app
from kyc.db import models as m
from kyc.seed.reference import load_prototype
from kyc.services import store, worker

from test_worker import only_halcyon

PROTO = load_prototype()
REF = "RA-2026-0412"
MAKER, CHECKER, OTHER_MAKER = "priya.nair", "daniel.tan", "aaron.koh"


def as_(user):
    return {"X-User": user}


@pytest.fixture
def client(db, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "UPLOADS_DIR", tmp_path)

    def clock(s, row):  # people act an hour after the case's last event, like Engine.submit_human
        last = s.scalar(select(func.max(m.CaseEvent.occurred_at)).where(m.CaseEvent.case_id == row.id))
        return store.local(last or row.opened_at) + timedelta(hours=1)

    app = create_app()
    app.state.session_factory = lambda: contextlib.nullcontext(db)
    app.state.clock = clock
    return TestClient(app)


def run_worker(db):
    worker.run_until_idle(db, worker.sim_clock)


def script(role, i):
    step = dict(PROTO["human_script"][role][i])
    step.pop("at"), step.pop("event")
    return step


# ============================================================== who and when
def test_requires_a_known_user(client):
    assert client.get("/cases").status_code == 401
    assert client.get("/cases", headers=as_("nobody")).status_code == 401
    assert client.get("/me", headers=as_(CHECKER)).json()["role"] == "checker"
    assert {u["role"] for u in client.get("/users").json()} == {"maker", "checker", "admin"}


def test_case_list_filters(client, db):
    total = db.scalar(select(func.count()).select_from(m.Case))
    assert client.get("/cases", headers=as_(MAKER)).json()["total"] == total
    mine = client.get("/cases", params={"assigned": True}, headers=as_(OTHER_MAKER)).json()["items"]
    assert mine and all(c["maker"]["username"] == OTHER_MAKER for c in mine)
    assert client.get("/cases", params={"q": "halcyon marine"}, headers=as_(MAKER)).json()["total"] == 1
    assert client.get("/cases", params={"status": "CLOSED"}, headers=as_(MAKER)).json()["total"] == 0
    assert client.get("/cases", params={"waiting_on_me": True}, headers=as_(MAKER)).json()["total"] == 0  # worker not run yet


def test_halcyon_over_http(client, db, tmp_path):
    only_halcyon(db)
    run_worker(db)
    d = client.get(f"/cases/{REF}", headers=as_(MAKER)).json()
    assert (d["status"], d["waiting_on"]) == ("WITH_MAKER", "maker")
    assert set(d["open_items"]) == {"ubo.2", "screening.hits"}
    assert client.get("/cases", params={"waiting_on_me": True}, headers=as_(MAKER)).json()["total"] == 1

    # wrong person, wrong moment, bad content
    first = script("maker", 0)
    assert client.post(f"/cases/{REF}/checker/approve", json={"final_rating": "MEDIUM"}, headers=as_(CHECKER)).status_code == 409
    assert client.post(f"/cases/{REF}/maker/submit", json=first, headers=as_(OTHER_MAKER)).status_code == 403
    assert client.post(f"/cases/{REF}/maker/submit", json=first, headers=as_(CHECKER)).status_code == 403
    bad = client.post(f"/cases/{REF}/maker/submit", headers=as_(MAKER),
                      json={**first, "uploads": [{**first["uploads"][0], "item": "bogus"}], "dispositions": {"nope": "x"}})
    assert bad.status_code == 422 and len(bad.json()["detail"]) == 2
    assert client.post(f"/cases/{REF}/maker/submit", json={}, headers=as_(MAKER)).status_code == 422  # open items left

    # maker submits (doc id left for the API to assign); a second submit is refused until the worker has run
    first["uploads"] = [{k: v for k, v in u.items() if k != "doc_id"} for u in first["uploads"]]
    r = client.post(f"/cases/{REF}/maker/submit", json=first, headers=as_(MAKER))
    assert r.status_code == 202, r.text
    assert client.post(f"/cases/{REF}/maker/submit", json=first, headers=as_(MAKER)).status_code == 409
    run_worker(db)
    d = client.get(f"/cases/{REF}", headers=as_(CHECKER)).json()
    assert (d["status"], d["waiting_on"]) == ("WITH_CHECKER", "checker")
    assert any(doc["doc_ref"] == "DOC-08" and doc["selected"] for doc in d["documents"])
    assert next(f for f in d["fields"] if f["key"] == "ubo.2")["confirmed_by"]["username"] == MAKER

    # checker returns Q7; the maker gets a suggested redraft
    assert client.post(f"/cases/{REF}/checker/return", json={"returned": [{"q_id": "Q99", "comment": "x"}]},
                       headers=as_(CHECKER)).status_code == 422
    assert client.post(f"/cases/{REF}/checker/return", json=script("checker", 0), headers=as_(CHECKER)).status_code == 202
    run_worker(db)
    d = client.get(f"/cases/{REF}", headers=as_(MAKER)).json()
    assert d["status"] == "RETURNED" and [s["q_id"] for s in d["suggestions"]] == ["Q7"]

    # maker uploads the ownership evidence; checker approves (the maker cannot)
    assert client.post(f"/cases/{REF}/maker/submit", json=script("maker", 1), headers=as_(MAKER)).status_code == 202
    run_worker(db)
    assert client.post(f"/cases/{REF}/checker/approve", json={"final_rating": "MEDIUM"}, headers=as_(MAKER)).status_code == 403
    assert client.post(f"/cases/{REF}/checker/approve", json=script("checker", 1), headers=as_(CHECKER)).status_code == 202
    run_worker(db)

    d = client.get(f"/cases/{REF}", headers=as_(MAKER)).json()
    assert (d["status"], d["waiting_on"]) == ("CLOSED", None)
    assert d["final"]["rating"] == "MEDIUM" and d["final"]["approved_by"]["username"] == CHECKER
    assert d["checker_returns"][0]["resolved"] is True

    # same answers and the same route as the in-memory prototype run
    e = Engine(PROTO, tmp_path / "mem")
    with contextlib.redirect_stdout(io.StringIO()):
        e.run()
    keys = ("q_id", "answer", "score", "score_reason", "citations", "flags", "confidence")
    assert [{k: a[k] for k in keys} for a in d["answers"]] == [{k: e.case.answers[q][k] for k in keys} for q in sorted(e.case.answers)]
    tl = client.get(f"/cases/{REF}/timeline", headers=as_(MAKER)).json()["events"]
    assert all(ev["processed"] for ev in tl)
    assert [ev["decision"]["rules_matched"] for ev in tl] == \
        [r["decision"]["rules_matched"] for r in e.audit if r["kind"] == "master_decision"]

    q7 = client.get(f"/cases/{REF}/answers/Q7", headers=as_(MAKER)).json()["versions"]
    assert [v["mode"] for v in q7][-1] == "redraft" and q7[-1]["is_current"] and "suggest" in [v["mode"] for v in q7]

    doc8 = next(doc for doc in d["documents"] if doc["doc_ref"] == "DOC-08")
    assert "Revenue FY2025" in client.get(f"/documents/{doc8['id']}", headers=as_(CHECKER)).json()["text"]

    # live stream: a closed case sends its state once and ends
    with client.stream("GET", f"/cases/{REF}/stream", headers=as_(MAKER)) as s:
        body = "".join(s.iter_text())
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert len(events) == 1 and events[0]["status"] == "CLOSED"


def test_unknown_case(client):
    assert client.get("/cases/RA-0000-0000", headers=as_(MAKER)).status_code == 404
