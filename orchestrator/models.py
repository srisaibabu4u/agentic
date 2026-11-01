"""Core data types shared by the master, the agents and the engine."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

STATUSES = ["NEW", "DISCOVERY", "INGESTION", "IN_DRAFT", "WITH_MAKER", "AUTO_REVIEW",
            "WITH_CHECKER", "RETURNED", "APPROVED", "CLOSED"]

A1, A2, A3, A4, A5 = "A1_DISCOVERY", "A2_INGEST", "A3_DRAFT", "A4_REVIEW", "A5_NOTIFY"
AGENT_NAMES = {A1: "Agent 1 · Discovery", A2: "Agent 2 · Ingestion", A3: "Agent 3 · Drafting",
               A4: "Agent 4 · Review", A5: "Agent 5 · Notification"}

LIVE_NAMES = {"A1": A1, "A2": A2, "A3": A3, "A4": A4, "A5": A5}


def parse_live(spec: str) -> tuple[set[str], bool]:
    """--live value -> (agents that call Claude, whether the master does).
    'A3,A4' some agents; 'master' the master; 'all' every agent and the master."""
    names = {x.strip().upper() for x in (spec or "").split(",") if x.strip()}
    if "ALL" in names:
        return set(LIVE_NAMES.values()), True
    unknown = names - set(LIVE_NAMES) - {"MASTER"}
    if unknown:
        raise ValueError(f"unknown --live name(s) {sorted(unknown)}; use A1..A5, master or all")
    return {LIVE_NAMES[n] for n in names - {"MASTER"}}, "MASTER" in names


# Status the case moves to when the master dispatches work to an agent.
STATUS_ON_DISPATCH = {A1: "DISCOVERY", A2: "INGESTION", A3: "IN_DRAFT", A4: "AUTO_REVIEW"}


@dataclass
class Task:
    """The envelope the master sends to one agent."""
    task_id: str
    agent: str
    scope: dict
    inputs: dict = field(default_factory=dict)
    depends_on: list = field(default_factory=list)
    retry: int = 0


@dataclass
class Event:
    """Anything that happened: an agent result or a human action."""
    type: str            # RENEWAL_DUE | AGENT_RESULT | MAKER_SUBMIT | CHECKER_RETURN | CHECKER_APPROVE
    source: str          # agent id, "maker", "checker" or "scheduler"
    payload: dict
    task_id: str | None = None


@dataclass
class Decision:
    """What the master returns after evaluating one event."""
    decision: str                    # DISPATCH | WAIT | CLOSE | ESCALATE
    new_status: str
    tasks: list[Task]
    rules_matched: list[str]
    guardrails_triggered: list[str]
    rationale: str
    meta: dict = field(default_factory=dict)   # agentic master only: who decided, its trace, the playbook's view

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tasks"] = [asdict(t) for t in self.tasks]
        if not d["meta"]:
            del d["meta"]
        return d


@dataclass
class Case:
    """Single source of truth for one assessment. Only the engine writes to it."""
    case_id: str
    meta: dict
    status: str = "NEW"
    manifest: dict | None = None
    docs: dict = field(default_factory=dict)          # doc_id -> document (metadata + text)
    fields: dict = field(default_factory=dict)        # field_id -> {value, confidence, source, ...}
    open_items: dict = field(default_factory=dict)    # ref -> reason (things only a human can close)
    maker_inputs: dict = field(default_factory=lambda: {"edits": {}, "dispositions": {}})
    answers: dict = field(default_factory=dict)       # q_id -> answer dict
    rating: dict | None = None
    findings: list = field(default_factory=list)
    checker_returns: list = field(default_factory=list)
    maker_submitted: bool = False
    final: dict | None = None
    retries: dict = field(default_factory=dict)       # "agent:item" -> count
    pending: list = field(default_factory=list)       # tasks dispatched but not finished
    task_seq: int = 0

    def next_task_id(self) -> str:
        self.task_seq += 1
        return f"{self.case_id}-T{self.task_seq:02d}"

    def snapshot(self) -> dict:
        """What the master reads. Kept compact on purpose."""
        return {
            "status": self.status,
            "docs": sorted(self.docs),
            "missing": [m["checklist_item"] for m in (self.manifest or {}).get("missing", [])],
            "fields": len(self.fields),
            "open_items": dict(self.open_items),
            "answers": sorted(self.answers),
            "findings": [f"{f['q_id']}:{f['severity']}" for f in self.findings],
            "maker_submitted": self.maker_submitted,
            "pending_tasks": [t.task_id + ":" + t.agent for t in self.pending],
        }
