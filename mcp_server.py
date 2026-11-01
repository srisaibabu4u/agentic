"""MCP server: exposes the KYC renewal workflow as tools for an agent-mode chat
(VS Code Copilot agent mode, Claude Code, or any MCP client).

The chat acts as the operator. It starts a case, lets the master and agents run
until a human is needed, and then submits maker or checker actions on the user's
instruction. Every master decision and agent result is kept in the audit log.

Run directly for a smoke test:  python mcp_server.py   (speaks MCP over stdio)
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from orchestrator.engine import Engine  # noqa: E402
from orchestrator.models import AGENT_NAMES  # noqa: E402

INSTRUCTIONS = (Path(__file__).resolve().parent / "AGENTS.md").read_text()
mcp = _Server("kyc-orchestrator", instructions=INSTRUCTIONS)
CASES: dict[str, Engine] = {}
AGENT_PROMPTS = {"master": "master.md", "a1": "a1_discovery.md", "a2": "a2_ingestion.md",
                 "a3": "a3_drafting.md", "a4": "a4_review.md", "a5": "a5_notification.md"}


# ----------------------------------------------------------------- helpers
def _engine(case_id: str) -> Engine:
    if case_id not in CASES:
        raise ToolError(f"Unknown case {case_id}. Open cases: {sorted(CASES) or 'none'}. Use start_case first.")
    return CASES[case_id]


def _step(eng: Engine, fn, *a, **kw) -> dict:
    """Run fn while capturing the console trace, then persist and summarise."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        stop = fn(*a, **kw)
        eng.finish()
    return {"stopped_because": stop, **_status(eng), "trace": buf.getvalue()}


def _submit(eng: Engine, role: str, event: str, payload: dict):
    try:
        eng.submit_human(role, event, payload)
    except ValueError as e:
        raise ToolError(str(e)) from e


def _status(eng: Engine) -> dict:
    c = eng.case
    slots_missing = [m["label"] for m in (c.manifest or {}).get("missing", [])
                     if not any(d["item"] == m["checklist_item"] and d.get("fresh", True) for d in c.docs.values())]
    return {
        "case_id": c.case_id, "client": c.meta["legal_name"], "status": c.status,
        "waiting_on": eng.waiting_on(), "simulated_time": eng.now.isoformat(),
        "open_items": c.open_items, "missing_documents": slots_missing,
        "pending_tasks": [f"{t.task_id} {AGENT_NAMES[t.agent]}" for t in c.pending],
        "rating": c.rating, "final": c.final,
        "checker_returns_open": [r for r in c.checker_returns if not r.get("resolved")],
    }


def _load_scenario(name: str) -> dict:
    p = Path(name) if name.endswith(".json") else ROOT / "data" / f"{name}.json"
    if not p.is_absolute():
        p = ROOT / p if not p.exists() else p
    return json.loads(p.read_text())


# ----------------------------------------------------------------- tools
@mcp.tool()
def list_scenarios() -> list[str]:
    """List the dummy case scenarios available in data/."""
    return sorted(p.stem for p in (ROOT / "data").glob("*.json"))


@mcp.tool()
def start_case(scenario: str = "sample_case", mode: str = "interactive", live_agents: str = "") -> dict:
    """Open a renewal case and let the master + agents run until a human is needed.

    mode: "interactive" (you act as maker/checker via maker_submit / checker_* tools)
          or "scripted" (plays the maker/checker actions stored in the scenario, runs to the end).
    live_agents: comma list of A1..A5 and/or "master", or "all", to call Claude for those (needs ANTHROPIC_API_KEY).
    Starting a case with an id that is already open replaces it.
    """
    cfg = _load_scenario(scenario)
    from orchestrator.models import parse_live
    llm, (live, live_master) = None, parse_live(live_agents)
    if live or live_master:
        from orchestrator.llm import LLM
        llm = LLM()
    cid = cfg["case"]["case_id"]
    eng = Engine(cfg, ROOT / "out" / "mcp" / cid, llm=llm, live_agents=live, live_master=live_master)
    CASES[cid] = eng
    buf = io.StringIO()
    with redirect_stdout(buf):
        eng.start()
    res = _step(eng, eng.advance, use_script=(mode == "scripted"))
    res["trace"] = buf.getvalue() + res["trace"]
    return res


@mcp.tool()
def get_case_status(case_id: str) -> dict:
    """Current status, who the case is waiting on, open items, missing documents and rating."""
    return _status(_engine(case_id))


@mcp.tool()
def get_answers(case_id: str) -> dict:
    """Drafted questionnaire answers with score, flags, citations and confidence, plus any suggestions."""
    e = _engine(case_id)
    return {"answers": e.case.answers, "rating": e.case.rating, "suggestions": e.suggestions,
            "latest_review_findings": e.case.findings}


@mcp.tool()
def get_fields(case_id: str) -> dict:
    """Extracted fields with value, confidence and source page; and documents on file."""
    e = _engine(case_id)
    return {"fields": e.case.fields,
            "documents": {k: {x: v.get(x) for x in ("type", "item", "date", "source", "fresh")} for k, v in e.case.docs.items()}}


@mcp.tool()
def explain_decisions(case_id: str, last_n: int = 5) -> list[dict]:
    """The master's most recent routing decisions: trigger, state it read, rules and guardrails, rationale, tasks."""
    recs = [a for a in _engine(case_id).audit if a["kind"] == "master_decision"][-last_n:]
    return [{"time": r["t"], "trigger": r["trigger"], "state_read": r["state"],
             "decision": r["decision"]["decision"], "new_status": r["decision"]["new_status"],
             "rules": r["decision"]["rules_matched"], "guardrails": r["decision"]["guardrails_triggered"],
             "rationale": r["decision"]["rationale"],
             "decided_by": r["decision"].get("meta", {}).get("decided_by", "playbook"),
             "playbook": r["decision"].get("meta", {}).get("playbook"),
             "tasks": [f"{t['task_id']} {AGENT_NAMES[t['agent']]} {t['scope']}" for t in r["decision"]["tasks"]]}
            for r in recs]


@mcp.tool()
def get_notifications(case_id: str) -> list[dict]:
    """Messages Agent 5 has sent on this case (subject, recipient, body)."""
    return _engine(case_id).outbox


@mcp.tool()
def maker_submit(case_id: str, uploads: list[dict] | None = None, field_edits: dict | None = None,
                 dispositions: dict | None = None) -> dict:
    """Submit the maker's work, then let the agents continue until a human is needed again.

    uploads: list of documents, each {"doc_id", "type", "item", "date", and "text" or "file_path"}.
        item must be a known document item (see dependency_map.doc_item_to_fields in the scenario),
        e.g. "audited_fs" (also needs "fy_end"), "ubo_corporate_register", "sow_declaration".
        text should use page markers like "[p1]".
    field_edits: {"<field_id>": {"value": ..., "note": "<why / evidence>"}} to confirm or correct a field.
    dispositions: {"screening.hits": "<rationale>"} to disposition a screening hit.
    """
    e = _engine(case_id)
    known = set(e.cfg["dependency_map"]["doc_item_to_fields"])
    docs = []
    for u in uploads or []:
        u = dict(u)
        if "file_path" in u:
            fp = Path(u.pop("file_path"))
            u["text"] = (fp if fp.is_absolute() else ROOT / fp).read_text()
        if u.get("item") not in known:
            raise ToolError(f"Unknown document item {u.get('item')!r}. Known: {sorted(known)}")
        for k in ("doc_id", "type", "date", "text"):
            if k not in u:
                raise ToolError(f"Upload is missing '{k}'")
        u.setdefault("source", "Maker upload")
        docs.append(u)
    payload = {"uploads": docs, "field_edits": field_edits or {}, "dispositions": dispositions or {}}
    _submit(e, "maker", "MAKER_SUBMIT", payload)
    return _step(e, e.advance, use_script=False)


@mcp.tool()
def checker_return(case_id: str, returns: list[dict]) -> dict:
    """Checker sends questions back to the maker. returns: [{"q_id": "Q7", "comment": "..."}]."""
    e = _engine(case_id)
    _submit(e, "checker", "CHECKER_RETURN", {"returned": returns})
    return _step(e, e.advance, use_script=False)


@mcp.tool()
def checker_approve(case_id: str, comment: str = "", final_rating: str | None = None) -> dict:
    """Checker approves. final_rating defaults to the proposed band (LOW, MEDIUM or HIGH)."""
    e = _engine(case_id)
    if e.waiting_on() != "checker":
        raise ToolError(f"Case is in status {e.case.status} and is waiting on "
                        f"{e.waiting_on() or 'agents'}; the checker cannot approve now.")
    band = final_rating or (e.case.rating or {}).get("band")
    if band not in ("LOW", "MEDIUM", "HIGH"):
        raise ToolError(f"Cannot approve with rating {band!r}; give final_rating explicitly.")
    _submit(e, "checker", "CHECKER_APPROVE", {"final_rating": band, "comment": comment})
    return _step(e, e.advance, use_script=False)


@mcp.tool()
def get_prompt(agent: str) -> str:
    """System prompt and user template for 'master', 'a1'..'a5'."""
    if agent not in AGENT_PROMPTS:
        raise ToolError(f"agent must be one of {list(AGENT_PROMPTS)}")
    return (ROOT / "prompts" / AGENT_PROMPTS[agent]).read_text()


@mcp.tool()
def run_tests() -> str:
    """Run the routing test suite and return its output."""
    r = subprocess.run([sys.executable, str(ROOT / "tests" / "test_routing.py")], capture_output=True, text=True, cwd=ROOT)
    return r.stdout + r.stderr


if __name__ == "__main__":
    mcp.run()
