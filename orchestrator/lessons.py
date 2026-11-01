"""Lessons from past cases.

    candidates(case_ref, findings, checker_returns) -> proposed lessons from one closed case
    attach(tasks, lessons)                          -> give approved lessons to the tasks they concern

A lesson is a short piece of advice learned from a closed case, for example
"Q6: Disposition must state the screening list name and record ID (policy 7.2.4)".
Two things become lessons:

* review findings the agents had to fix themselves (evidence on file, not HIGH):
  advice for drafting, so the next draft gets it right first time;
* checker returns: advice for drafting and review, because the review should
  have caught what the checker caught.

Data problems (a HIGH finding, an open item, stale evidence) are not lessons;
the maker fixes those per case.

Lessons are advice, never authority. A person approves each one before it is
used. Code attaches approved lessons to the tasks they concern and records
them in the task's inputs, so the audit shows exactly what each agent was told.
Lessons cannot change policy, the master's guardrails or the result checks.
"""
from __future__ import annotations

import re

from .models import A3, A4, Task

DRAFTING_CHECKS = {"C2", "C5", "C8"}   # completeness, scoring, language: what a better draft avoids
MAX_PER_TASK = 5


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def candidates(case_ref: str, findings: list[dict], checker_returns: list[dict]) -> list[dict]:
    """Proposed lessons from one closed case, one per distinct problem."""
    out: dict[str, dict] = {}
    for f in findings:
        if f.get("severity") == "HIGH" or not (f.get("evidence_available") or f.get("check") in DRAFTING_CHECKS):
            continue
        ref = f.get("policy_ref")
        key = f"finding:{f['q_id']}:{f.get('check')}:{ref or _norm(f.get('issue', ''))[:60]}"
        text = f"{f['q_id']}: {f['issue'].rstrip('.')}." + (f" (policy {ref})" if ref else "")
        out.setdefault(key, {"key": key, "kind": "REVIEW_FINDING", "q_id": f["q_id"], "agents": [A3],
                             "policy_ref": ref, "text": text,
                             "evidence": [{"case_ref": case_ref, "source": f"review finding {f.get('check')}",
                                           "detail": f.get("issue", "")}]})
    for r in checker_returns:
        key = f"checker:{r['q_id']}:{_norm(r['comment'])[:80]}"
        text = f"{r['q_id']}: a checker returned this answer with: \"{r['comment'].strip()}\" Cover this in the first draft."
        out.setdefault(key, {"key": key, "kind": "CHECKER_RETURN", "q_id": r["q_id"], "agents": [A3, A4],
                             "policy_ref": None, "text": text,
                             "evidence": [{"case_ref": case_ref, "source": "checker return", "detail": r["comment"]}]})
    return list(out.values())


def relevant(lessons: list[dict], agent: str | None = None, questions: list[str] | None = None) -> list[dict]:
    """Approved lessons for an agent and questions, most often seen first."""
    hits = [x for x in lessons if (agent is None or agent in x["agents"])
            and (questions is None or x["q_id"] in questions)]
    return sorted(hits, key=lambda x: (-x.get("occurrences", 1), x["id"]))


def attach(tasks: list[Task], lessons: list[dict]) -> list[str]:
    """Add approved lessons to drafting and review tasks. Returns the lesson ids used."""
    used = []
    for t in tasks:
        if t.agent not in (A3, A4) or not lessons:
            continue
        pick = relevant(lessons, t.agent, t.scope.get("questions", []))[:MAX_PER_TASK]
        if pick:
            t.inputs["lessons"] = [{"id": x["id"], "q_id": x["q_id"], "text": x["text"]} for x in pick]
            used += [x["id"] for x in pick]
    return used
