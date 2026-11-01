"""Tools and result checks for the agentic (live) agents.

In live mode every agent runs its own tool-use loop: it decides what to look
up, calls the read-only tools below, checks its own work and finishes by
calling submit_result. The loop itself is in llm.py.

Two rules keep the agents safe to run unattended:

* tools only read. Nothing here can change the case; the engine still applies
  results, and the master still decides what happens next;
* submit_result is checked against the evidence (validate_*). A rejected
  submission goes back to the agent as a tool error so it can fix it. If it
  never passes, the task FAILS and the master's retry guardrail (G4) applies.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

from .master import LOW_CONFIDENCE
from .models import Case, Task


@dataclass
class Tool:
    name: str
    description: str
    params: dict                     # JSON schema "properties"
    fn: Callable[..., object]
    required: tuple = ()

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": {"type": "object", "properties": self.params, "required": list(self.required)}}


def _str(desc: str) -> dict:
    return {"type": "string", "description": desc}


def _meta(doc: dict) -> dict:
    """Document metadata without the text: what discovery tools return."""
    return {k: v for k, v in doc.items() if k != "text"}


def _pages(text: str) -> dict[int, str]:
    parts = re.split(r"\[p(\d+)\]", text)
    if len(parts) == 1:
        return {1: text}
    pages = {}
    if parts[0].strip():
        pages[1] = parts[0]
    for i in range(1, len(parts), 2):
        pages[int(parts[i])] = pages.get(int(parts[i]), "") + parts[i + 1]
    return pages


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


TURNOVER = re.compile(r"turnover\.fy\d{4}")
RISK_WORD = {1: "low", 2: "standard", 3: "elevated", 4: "high", 5: "very high"}


# ============================================================ Agent 1 · Discovery
def discovery_tools(agent, task: Task, case: Case, now: datetime) -> list[Tool]:
    docs = {d["doc_id"]: d for d in agent.search_all_sources(case)}
    due = date.fromisoformat(case.meta["due_date"][:10])

    def by_source(*sources):
        def run(checklist_item: str | None = None):
            hits = [_meta(d) for d in docs.values() if d.get("source") in sources
                    and (not checklist_item or d["item"] == checklist_item)]
            return {"documents": hits, "count": len(hits)}
        return run

    def search_crm():
        crm = [_meta(d) for d in docs.values() if d.get("source") == "CRM"]
        return {"client_profile": {k: case.meta.get(k) for k in ("client_id", "legal_name", "uen", "segment",
                                                                  "prior_rating", "prior_assessment_date")},
                "attachments": crm}

    def check_freshness(doc_id: str):
        if doc_id not in docs:
            raise ValueError(f"unknown doc_id {doc_id}")
        fresh, why = agent.is_fresh(docs[doc_id], now, due)
        return {"doc_id": doc_id, "fresh": fresh, "reason": why or "within the freshness rule"}

    item = {"checklist_item": _str("optional: only documents for this checklist item")}
    return [
        Tool("get_checklist", "The required-document checklist for this case, with identity_critical flags.", {},
             lambda: agent.cfg["checklist"]),
        Tool("search_dms", "Search the document management system (and maker uploads) for the client.", item,
             by_source("DMS", "Maker upload")),
        Tool("fetch_registry", "Latest company registry extract (ACRA BizFile) for the client.", item,
             by_source("ACRA")),
        Tool("run_screening", "Latest sanctions, PEP and adverse-media screening report for the client and parties.",
             item, by_source("WorldCheck")),
        Tool("search_crm", "Client profile and CRM attachments.", {}, search_crm),
        Tool("check_freshness", "Apply the bank's freshness rule to one document. Use it before marking fresh.",
             {"doc_id": _str("document id, e.g. DOC-03")}, check_freshness, ("doc_id",)),
    ]


def validate_discovery(agent, task, case, now, res: dict) -> list[str]:
    docs = {d["doc_id"]: d for d in agent.search_all_sources(case)}
    due = date.fromisoformat(case.meta["due_date"][:10])
    errs, items = [], [i["item"] for i in agent.cfg["checklist"]["items"]]
    found, missing = res.get("found", []), res.get("missing", [])
    covered = {f.get("checklist_item") for f in found} | {m.get("checklist_item") for m in missing}
    errs += [f"checklist item '{i}' is neither in found nor missing" for i in items if i not in covered]
    for f in found:
        d = docs.get(f.get("doc_id"))
        if d is None:
            errs.append(f"found doc_id {f.get('doc_id')} does not exist in any source")
            continue
        if f.get("checklist_item") != d["item"]:
            errs.append(f"{d['doc_id']} is a {d['item']}, not {f.get('checklist_item')}")
        fresh, why = agent.is_fresh(d, now, due)
        if bool(f.get("fresh")) != fresh:
            errs.append(f"{d['doc_id']} fresh={f.get('fresh')} but the freshness rule says {fresh} {why}".strip())
        if not fresh and d["item"] not in {m.get("checklist_item") for m in missing}:
            errs.append(f"{d['doc_id']} is stale, so '{d['item']}' must also be listed in missing")
    want = "PARTIAL" if missing else "DONE"
    if res.get("status") not in (want, "FAILED"):
        errs.append(f"status must be {want} when {len(missing)} item(s) are missing")
    return errs


# ============================================================ Agent 2 · Ingestion
def ingestion_tools(agent, task: Task, case: Case, now: datetime) -> list[Tool]:
    scope = task.scope["docs"]
    from .agents import EXTRACTORS

    def doc(doc_id):
        if doc_id not in scope:
            raise ValueError(f"{doc_id} is not in this task's scope {scope}")
        return case.docs[doc_id]

    def read_document(doc_id: str, page: int | None = None):
        pages = _pages(doc(doc_id)["text"])
        if page is not None:
            return {"doc_id": doc_id, "page": page, "text": pages.get(int(page), ""), "pages": sorted(pages)}
        return {"doc_id": doc_id, "pages": {f"p{p}": t for p, t in sorted(pages.items())}}

    def find_in_document(doc_id: str, pattern: str):
        text = doc(doc_id)["text"]
        hits = []
        for m in list(re.finditer(pattern, text, flags=re.I))[:20]:
            page = max([int(p.group(1)) for p in re.finditer(r"\[p(\d+)\]", text[:m.start()])] or [1])
            line = text[text.rfind("\n", 0, m.start()) + 1: (text.find("\n", m.end()) + 1 or len(text) + 1) - 1]
            hits.append({"match": m.group(0), "line": line.strip(), "source": f"{doc_id}#p{page}"})
        return {"hits": hits}

    schema = sorted({f for _, f, _, _ in EXTRACTORS})
    return [
        Tool("list_documents", "Metadata of the documents in scope for this task.", {},
             lambda: [_meta(doc(d)) for d in scope]),
        Tool("read_document", "Read a document in scope, whole or one page. Pages are marked [pN].",
             {"doc_id": _str("document id"), "page": {"type": "integer", "description": "optional page number"}},
             read_document, ("doc_id",)),
        Tool("find_in_document", "Case-insensitive regex search in one document; returns matching lines with page refs.",
             {"doc_id": _str("document id"), "pattern": _str("Python regex")}, find_in_document, ("doc_id", "pattern")),
        Tool("get_field_schema", "The field ids to extract. turnover.fy{yr} means one field per financial year.", {},
             lambda: {"fields": schema, "low_confidence_threshold": LOW_CONFIDENCE,
                      "maker_confirmed_fields": [k for k, v in case.fields.items() if v.get("source") == "maker"]}),
    ]


def validate_ingestion(agent, task, case, now, res: dict) -> list[str]:
    from .agents import EXTRACTORS
    schema = {f for _, f, _, _ in EXTRACTORS}
    scope, errs = task.scope["docs"], []
    fields, flags = res.get("fields") or {}, res.get("flags") or []
    if not isinstance(fields, dict):
        return ["fields must be an object keyed by field id"]
    for fid, f in fields.items():
        if fid not in schema and not TURNOVER.fullmatch(fid):
            errs.append(f"'{fid}' is not in the field schema")
        if not isinstance(f, dict) or "value" not in f:
            errs.append(f"{fid}: needs value, raw, confidence and source")
            continue
        conf = f.get("confidence")
        if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
            errs.append(f"{fid}: confidence must be a number from 0 to 1")
            continue
        m = re.fullmatch(r"([^#]+)#p(\d+)", str(f.get("source", "")))
        if not m or m.group(1) not in scope:
            errs.append(f"{fid}: source must be '<doc in scope>#p<page>', got {f.get('source')!r}")
        elif f.get("raw"):  # raw is the evidence quote (several quotes joined by ';'); each must be in the document
            text = _norm(case.docs[m.group(1)]["text"])
            if not all(_norm(p)[:60] in text for p in str(f["raw"]).split(";") if p.strip()):
                errs.append(f"{fid}: raw text is not found in {m.group(1)}; quote the document exactly")
        if conf < LOW_CONFIDENCE and not any(fl.get("code") == "LOW_CONF_FIELD" and fl.get("ref") == fid for fl in flags):
            errs.append(f"{fid}: confidence {conf} < {LOW_CONFIDENCE} needs a LOW_CONF_FIELD flag")
        f.setdefault("conflict", False)
        f.setdefault("note", "")
    for fl in flags:
        if fl.get("code") == "LOW_CONF_FIELD" and fl.get("ref") not in fields:
            errs.append(f"LOW_CONF_FIELD flag refers to {fl.get('ref')}, which is not in fields")
    if res.get("status") not in ("DONE", "FAILED"):
        errs.append("status must be DONE or FAILED")
    return errs


# ============================================================ Agent 3 · Drafting
def _reference_tools(agent, case: Case) -> list[Tool]:
    q = agent.cfg["questionnaire"]

    def get_field(field_id: str):
        if field_id not in case.fields:
            return {"field_id": field_id, "exists": False}
        return {"field_id": field_id, "exists": True, **case.fields[field_id],
                "open_item": case.open_items.get(field_id)}

    def country_risk(codes: list[str]):
        return {c: q["country_risk"].get(c, 3) for c in codes} | {"_default_if_unlisted": 3}

    def industry_risk(ssic_code: str):
        entry = q.get("industry_risk", {}).get(ssic_code)
        if not entry:
            return {"ssic_code": ssic_code, "listed": False, "note": "not on the list: treat as elevated (3)"}
        return {"ssic_code": ssic_code, "listed": True, "label": entry[0], "risk_level": entry[1],
                "word": RISK_WORD[entry[1]]}

    return [
        Tool("list_fields", "Every extracted field: id, value, confidence, source and whether it is an open item.", {},
             lambda: {k: {"value": v.get("value"), "confidence": v.get("confidence"), "source": v.get("source"),
                          "open_item": case.open_items.get(k)} for k, v in sorted(case.fields.items())}),
        Tool("get_field", "One field in full (value, raw quote, note, confidence, source).",
             {"field_id": _str("field id")}, get_field, ("field_id",)),
        Tool("get_maker_inputs", "Field values the maker confirmed and their screening dispositions.", {},
             lambda: case.maker_inputs),
        Tool("get_open_items", "Items only a person can close (low-confidence fields, open screening hits).", {},
             lambda: case.open_items),
        Tool("lookup_country_risk", "Country risk level 1-5 for country codes.",
             {"codes": {"type": "array", "items": {"type": "string"}}}, country_risk, ("codes",)),
        Tool("lookup_industry_risk", "Industry risk for an SSIC code.", {"ssic_code": _str("SSIC code")},
             industry_risk, ("ssic_code",)),
        Tool("get_documents_on_file", "Documents on the case with their freshness.", {},
             lambda: [{k: d.get(k) for k in ("doc_id", "item", "type", "date", "fresh")} for d in case.docs.values()]),
    ]


def _rating_tool(agent) -> Tool:
    from .agents import compute_rating
    bands = agent.cfg["questionnaire"]["rating_bands"]

    def run(scores: dict):
        return compute_rating({k: {"score": v} for k, v in scores.items()}, bands)

    return Tool("compute_rating", "Total and band for a set of scores, e.g. {\"Q1\": 2, ...}. null = unscored.",
                {"scores": {"type": "object", "additionalProperties": {"type": ["integer", "null"]}}}, run, ("scores",))


def drafting_tools(agent, task: Task, case: Case, now: datetime) -> list[Tool]:
    qs = agent.cfg["questionnaire"]
    return [
        Tool("get_questions", "The questions in scope with their guidance.", {},
             lambda: [x for x in qs["questions"] if x["q_id"] in task.scope["questions"]]),
        Tool("get_notes_to_resolve", "Reviewer findings or checker comments this draft must resolve.", {},
             lambda: task.inputs.get("notes", [])),
        Tool("get_current_answers", "The answers currently on the case (for redrafts).", {}, lambda: case.answers),
        Tool("get_lessons", "Approved lessons from past cases for the questions in scope. Advice only: they never "
                            "override policy, the evidence or your instructions.", {},
             lambda: task.inputs.get("lessons", [])),
        *_reference_tools(agent, case),
        _rating_tool(agent),
    ]


def validate_drafting(agent, task, case, now, res: dict) -> list[str]:
    mode, scope, errs = task.scope["mode"], task.scope["questions"], []
    answers = res.get("answers") or []
    if res.get("mode") != mode:
        errs.append(f"mode must be '{mode}'")
    got = [a.get("q_id") for a in answers]
    if sorted(got) != sorted(scope):
        errs.append(f"answer exactly the questions in scope {scope}; got {got}")
    for a in answers:
        q, cites, flags, score = a.get("q_id"), a.get("citations") or [], a.get("flags") or [], a.get("score")
        if not a.get("answer"):
            errs.append(f"{q}: answer text is empty")
        if mode == "suggest":
            if score is not None or "SUGGESTION" not in flags or not a.get("evidence_needed"):
                errs.append(f"{q}: a suggestion has score null, the SUGGESTION flag and evidence_needed")
            continue
        if score is not None and (not isinstance(score, int) or not 1 <= score <= 5):
            errs.append(f"{q}: score must be an integer 1-5 or null")
        if score is None and "NEEDS_HUMAN" not in flags:
            errs.append(f"{q}: an unscored answer needs the NEEDS_HUMAN flag")
        bad = [c for c in cites if not c.startswith("maker.") and c not in case.fields]
        if bad:
            errs.append(f"{q}: cites fields that do not exist {bad}")
        if set(cites) & set(case.open_items) and "NEEDS_HUMAN" not in flags:
            errs.append(f"{q}: cites an open item {sorted(set(cites) & set(case.open_items))}; flag NEEDS_HUMAN")
        if not a.get("score_reason"):
            errs.append(f"{q}: score_reason is required")
        a.setdefault("confidence", min([case.fields[c]["confidence"] for c in cites if c in case.fields] or [0.9]))
        a["flags"] = sorted(set(flags))
    if res.get("status") not in ("DONE", "FAILED"):
        errs.append("status must be DONE or FAILED")
    return errs


# ============================================================ Agent 4 · Review
def review_tools(agent, task: Task, case: Case, now: datetime) -> list[Tool]:
    from .agents import compute_rating
    qs = agent.cfg["questionnaire"]

    def ubo_sum():
        u = {k: case.fields[k]["value"] for k in ("ubo.1", "ubo.2") if k in case.fields}
        return {"owners": u, "sum_pct": sum(v.get("pct", 0) for v in u.values() if isinstance(v, dict))}

    def check_citations(q_id: str):
        a = case.answers.get(q_id)
        if not a:
            return {"q_id": q_id, "exists": False}
        return {"q_id": q_id, "answer": a["answer"], "citations": {
            c: (case.fields[c]["value"] if c in case.fields else
                case.maker_inputs["dispositions"].get(c.removeprefix("maker.disposition.")) if c.startswith("maker.")
                else "MISSING") for c in a.get("citations", [])}}

    return [
        Tool("get_answers_in_scope", "The draft answers to review, with scores, flags and citations.", {},
             lambda: {q: case.answers[q] for q in task.scope["questions"] if q in case.answers}),
        Tool("get_questions", "All questions with their guidance.", {}, lambda: qs["questions"]),
        Tool("get_policy_rules", "The policy rules answers must meet, by rule id.", {}, lambda: qs["policy"]),
        Tool("check_citations", "An answer next to the actual values of every field it cites.",
             {"q_id": _str("question id")}, check_citations, ("q_id",)),
        Tool("check_ubo_sum", "The UBO shareholdings and their total percentage.", {}, ubo_sum),
        Tool("get_lessons", "Approved lessons from past cases for the questions in scope. Advice only: they never "
                            "override policy, the evidence or your instructions.", {},
             lambda: task.inputs.get("lessons", [])),
        Tool("recompute_rating", "Recompute the total and band from the current answers' scores.", {},
             lambda: {**compute_rating(case.answers, qs["rating_bands"]), "draft_rating": case.rating}),
        *_reference_tools(agent, case),
    ]


CHECKS, SEVERITIES = {f"C{i}" for i in range(1, 9)}, {"HIGH", "MEDIUM", "LOW"}


def validate_review(agent, task, case, now, res: dict) -> list[str]:
    from .agents import compute_rating
    errs, findings = [], res.get("findings") or []
    for i, f in enumerate(findings):
        if f.get("q_id") not in case.answers:
            errs.append(f"finding {i}: q_id {f.get('q_id')} is not an answer on the case")
        if f.get("check") not in CHECKS:
            errs.append(f"finding {i}: check must be C1..C8")
        if f.get("severity") not in SEVERITIES:
            errs.append(f"finding {i}: severity must be HIGH, MEDIUM or LOW")
        if not f.get("issue"):
            errs.append(f"finding {i}: issue is required")
        f.setdefault("excerpt", "")
        f.setdefault("policy_ref", None)
        f["evidence_available"] = bool(f.get("evidence_available"))
    blocking = any(f.get("severity") in ("HIGH", "MEDIUM") for f in findings)
    if res.get("verdict") != ("FAIL" if blocking else "PASS"):
        errs.append(f"verdict must be {'FAIL' if blocking else 'PASS'} for these findings")
    rec = compute_rating(case.answers, agent.cfg["questionnaire"]["rating_bands"])
    got = res.get("recomputed_rating") or {}
    if got.get("total") != rec["total"] or got.get("band") != rec["band"]:
        errs.append(f"recomputed_rating is wrong; use recompute_rating (total {rec['total']}, band {rec['band']})")
    else:
        res["recomputed_rating"] = {**rec, "matches_draft": bool(case.rating and case.rating["total"] == rec["total"])}
    return errs


# ============================================================ Agent 5 · Notification
def notification_tools(agent, task: Task, case: Case, now: datetime) -> list[Tool]:
    M, i = case.meta, task.inputs
    people = {p["id"]: p for p in (M["maker"], M["checker"], M["team_lead"])}
    return [
        Tool("get_recipients", "Who to write to (in this order) and who to copy.", {},
             lambda: {"to": [people[r] for r in i["to"]],
                      "cc": [people[i["escalate_cc"]]] if i.get("escalate_cc") else []}),
        Tool("get_case_summary", "Case id, client, due date, status, link and business days left.", {},
             lambda: {"case_id": case.case_id, "client": M["legal_name"], "short_name": M["short_name"],
                      "due_date": M["due_date"], "status": case.status, "link": M["case_url"],
                      "escalated": bool(i.get("escalate_cc"))}),
        Tool("get_action_items", "The template and the action items the message must list.", {},
             lambda: {"template": i["template"], "items": i["items"], "context": i.get("context", {})}),
    ]


def validate_notification(agent, task, case, now, res: dict) -> list[str]:
    M, i, errs = case.meta, task.inputs, []
    sent = res.get("sent") or []
    if len(sent) != len(i["to"]):
        return [f"send exactly one message per recipient, in order: {i['to']}"]
    for n, msg in enumerate(sent):
        for k in ("subject", "body"):
            if not msg.get(k):
                errs.append(f"message {n}: {k} is required")
        body = msg.get("body") or ""
        if case.case_id not in body or M["case_url"] not in body:
            errs.append(f"message {n}: the body must state the case id {case.case_id} and the link {M['case_url']}")
        missing = [it for it in i["items"] if _norm(it)[:40] not in _norm(body)]
        if missing:
            errs.append(f"message {n}: list every action item; missing {missing}")
        msg.setdefault("channel", "task_inbox+email")
        msg.setdefault("message_id", f"m-{case.task_seq:03d}{n}")
    res.setdefault("action_items", i["items"])
    return errs
