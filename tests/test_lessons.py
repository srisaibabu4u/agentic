"""Lessons from past cases: what becomes a lesson, who gets it, and what it changes.

Run:  python -m pytest -q tests/test_lessons.py
"""
import copy
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator import lessons  # noqa: E402
from orchestrator.agents import DraftingAgent, ReviewAgent  # noqa: E402
from orchestrator.engine import Engine  # noqa: E402
from orchestrator.models import A3, A4, A5, Case, Task  # noqa: E402

CFG = json.loads((ROOT / "data" / "sample_case.json").read_text())
Q6 = {"id": "L-0001", "q_id": "Q6", "agents": [A3], "occurrences": 3, "kind": "REVIEW_FINDING", "policy_ref": "7.2.4",
      "text": "Q6: Disposition must state the screening list name and record ID. (policy 7.2.4)"}
Q7 = {"id": "L-0002", "q_id": "Q7", "agents": [A3, A4], "occurrences": 1, "kind": "CHECKER_RETURN", "policy_ref": None,
      "text": "Q7: for a corporate shareholder, identify its ultimate owners and their source of wealth."}


def run(cfg, tmp):
    eng = Engine(cfg, Path(tmp))
    with redirect_stdout(io.StringIO()):
        eng.run()
    return eng


def rules(eng):
    return [r for a in eng.audit if a["kind"] == "master_decision" for r in a["decision"]["rules_matched"]]


def test_a_closed_case_proposes_its_two_lessons(tmp_path):
    got = {c["key"]: c for c in run(copy.deepcopy(CFG), tmp_path).lesson_candidates()}
    assert set(got) == {"finding:Q6:C2:7.2.4",
                        "checker:Q7:seacrest bvi holds 45 identify its ultimate owners and their source of wealth"}
    assert got["finding:Q6:C2:7.2.4"]["agents"] == [A3]                     # drafting should get it right first time
    assert got[next(k for k in got if k.startswith("checker"))]["agents"] == [A3, A4]  # review should catch it too


def test_data_problems_are_not_lessons():
    findings = [
        {"q_id": "Q2", "check": "C4", "severity": "HIGH", "issue": "UBO sum 90%", "evidence_available": True},
        {"q_id": "Q4", "check": "C7", "severity": "MEDIUM", "issue": "Relies on stale evidence", "evidence_available": False},
        {"q_id": "Q6", "check": "C3", "severity": "HIGH", "issue": "Open item", "evidence_available": False},
        {"q_id": "Q5", "check": "C2", "severity": "LOW", "issue": "State the currency.", "evidence_available": False},
        {"q_id": "Q5", "check": "C2", "severity": "LOW", "issue": "State the currency.", "evidence_available": False},
    ]
    got = lessons.candidates("X", findings, [])
    assert [c["q_id"] for c in got] == ["Q5"]  # completeness is a drafting habit; the duplicate counts once


def test_lessons_go_only_to_the_tasks_they_concern():
    tasks = [Task("T1", A3, {"questions": ["Q6", "Q7"], "mode": "redraft"}),
             Task("T2", A4, {"questions": ["Q1", "Q6", "Q7"]}),
             Task("T3", A5, {"template": "MAKER_REVIEW"}, {"items": ["x"]}),
             Task("T4", A3, {"questions": ["Q1"], "mode": "draft"})]
    used = lessons.attach(tasks, [Q7, Q6])
    assert [x["id"] for x in tasks[0].inputs["lessons"]] == ["L-0001", "L-0002"]  # most often seen first
    assert [x["id"] for x in tasks[1].inputs["lessons"]] == ["L-0002"]           # the Q6 lesson is for drafting
    assert "lessons" not in tasks[2].inputs and "lessons" not in tasks[3].inputs
    assert used == ["L-0001", "L-0002", "L-0002"]


def test_an_approved_lesson_saves_a_review_round(tmp_path):
    base = run(copy.deepcopy(CFG), tmp_path / "before")
    cfg = copy.deepcopy(CFG)
    cfg["lessons"] = [Q6]
    eng = run(cfg, tmp_path / "after")
    assert "R7" in rules(base) and "R7" not in rules(eng)  # Q6 is right first time, so no self-correction loop
    assert eng.case.status == "CLOSED" and eng.case.final["rating"] == base.case.final["rating"] == "MEDIUM"
    assert eng.case.answers["Q6"]["answer"] == base.case.answers["Q6"]["answer"]
    assert "resolves" not in eng.case.answers["Q6"]  # a lesson is advice, not a note being resolved
    given = [a["task"]["inputs"].get("lessons") for a in eng.audit if a["kind"] == "agent_result" and a["agent"] == A3]
    assert [{"id": "L-0001", "q_id": "Q6", "text": Q6["text"]}] in given  # the audit shows what the agent was told
    assert len(eng.audit) < len(base.audit)


def test_live_agents_can_read_their_lessons():
    case, now = Case("X", CFG["case"]), datetime.fromisoformat("2026-10-30T09:00:00+08:00")
    task = Task("T1", A3, {"questions": ["Q6"], "mode": "draft"}, {"lessons": [Q6]})
    for agent in (DraftingAgent(CFG), ReviewAgent(CFG)):
        tool = next(t for t in agent.tools(task, case, now) if t.name == "get_lessons")
        assert tool.fn() == [Q6]
