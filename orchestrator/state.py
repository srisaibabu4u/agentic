"""How agent results and human actions change a case.

The single place that writes to a Case. Shared by the in-memory Engine and the
database-backed engine in backend/kyc/services, so both apply exactly the same
rules. Outputs that are not case state (notifications, suggested redrafts) are
left to the caller.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .agents import compute_rating
from .models import A1, A2, A3, A4, Case, Event


def apply_result(case: Case, cfg: dict, agent: str, res: dict) -> None:
    c = case
    if res.get("status") == "FAILED":
        return
    if agent == A1:
        c.manifest = res
        store = {d["doc_id"]: d for d in cfg["document_store"]} | c.docs
        for f in res["found"]:
            c.docs[f["doc_id"]] = {**store[f["doc_id"]], "fresh": f["fresh"]}
    elif agent == A2:
        for fid, f in res["fields"].items():
            if c.fields.get(fid, {}).get("source") == "maker":
                continue  # never overwrite a value the maker confirmed
            c.fields[fid] = f
        for fl in res["flags"]:
            if fl["code"] == "LOW_CONF_FIELD":
                conf = res["fields"][fl["ref"]]["confidence"]
                c.open_items[fl["ref"]] = f"LOW_CONF_FIELD ({conf}): confirm value"
            elif fl["code"] == "SCREENING_HIT" and fl["ref"] not in c.maker_inputs["dispositions"]:
                c.open_items[fl["ref"]] = "SCREENING_HIT: disposition required"
    elif agent == A3:
        if res["mode"] != "suggest":  # suggestions are not case state
            for a in res["answers"]:
                c.answers[a["q_id"]] = a
                for r in c.checker_returns:
                    if r["q_id"] == a["q_id"] and res["mode"] == "redraft" and not r.get("resolved"):
                        r["resolved"] = True
            c.rating = compute_rating(c.answers, cfg["questionnaire"]["rating_bands"])
    elif agent == A4:
        c.findings = res["findings"]


def apply_human(case: Case, ev: Event, now: datetime) -> None:
    c, p = case, ev.payload
    if ev.type == "MAKER_SUBMIT":
        for d in p.get("uploads", []):
            c.docs[d["doc_id"]] = {**d, "fresh": True}
        for fid, e in p.get("field_edits", {}).items():
            c.fields[fid] = {**c.fields.get(fid, {}), "value": e["value"], "confidence": 1.0,
                             "source": "maker", "note": e["note"]}
            c.maker_inputs["edits"][fid] = e
            c.open_items.pop(fid, None)
        for ref, text in p.get("dispositions", {}).items():
            c.maker_inputs["dispositions"][ref] = text
            c.open_items.pop(ref, None)
        c.maker_submitted = True
    elif ev.type == "CHECKER_RETURN":
        c.checker_returns += [dict(r) for r in p["returned"]]
        c.maker_submitted = False
    elif ev.type == "CHECKER_APPROVE":
        months = {"LOW": 36, "MEDIUM": 12, "HIGH": 6}[p["final_rating"]]
        due = datetime.fromisoformat(c.meta["due_date"])
        nxt = due.replace(year=due.year + months // 12) if months >= 12 else due + timedelta(days=182)
        p["next_review"] = nxt.date().isoformat()
        c.final = {"rating": p["final_rating"], "score": c.rating, "approved_by": c.meta["checker"]["name"],
                   "approved_at": now.isoformat(), "next_review": p["next_review"], "comment": p.get("comment")}
