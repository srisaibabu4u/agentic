"""Agentic master, tested without an API key.

The master agent decides; code enforces the guardrails. These tests check that
* every decision the rule playbook makes is allowed by the guardrails, so a
  master agent that follows the playbook runs a case to the same result;
* each guardrail rejects a decision that breaks it, and the agent is told why;
* the loop, the code-decided waits and the playbook fallback work.

Run:  python -m pytest -q tests/test_master_agent.py
"""
import copy
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator import master_agent as ma  # noqa: E402
from orchestrator.engine import Engine  # noqa: E402
from orchestrator.llm import LLM  # noqa: E402
from orchestrator.master import Master  # noqa: E402
from orchestrator.models import A2, A3, A4, A5, Case, Event, Task, parse_live  # noqa: E402

CFG = json.loads((ROOT / "data" / "sample_case.json").read_text())
NOW = datetime.fromisoformat("2026-10-30T09:00:00+08:00")


def quiet(fn, *a, **kw):
    with redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


class PlaybookLLM:
    """Stands in for Claude: proposes what the playbook would, through the real guardrail check."""

    def __init__(self):
        self.master, self.calls = None, 0

    def run_agent(self, prompt_file, brief, tools, validate, **vars):
        self.calls += 1
        proposal = ma.to_proposal(self.master.reference)
        errors = validate(proposal)
        assert errors == [], (brief["event"], errors)
        return {**proposal, "agent_trace": {"turns": 1}}


@pytest.fixture
def playbook_llm(monkeypatch):
    llm, real = PlaybookLLM(), ma.AgenticMaster.decide

    def decide(self, case, ev, now):
        llm.master = self
        return real(self, case, ev, now)

    monkeypatch.setattr(ma.AgenticMaster, "decide", decide)
    return llm


# ------------------------------------------------------------------ the master agent runs whole cases
def test_master_agent_runs_the_sample_case_to_the_same_result(playbook_llm, tmp_path):
    eng = Engine(copy.deepcopy(CFG), tmp_path, llm=playbook_llm, live_master=True)
    quiet(eng.run)
    assert eng.case.status == "CLOSED" and eng.case.final["rating"] == "MEDIUM"
    for name in ("final_case.json", "outbox.json"):  # identical to the committed offline run
        assert json.loads((tmp_path / name).read_text()) == json.loads((ROOT / "out" / name).read_text()), name
    metas = [a["decision"].get("meta", {}) for a in eng.audit if a["kind"] == "master_decision"]
    by_agent = [x for x in metas if x.get("decided_by") == "master_agent"]
    assert playbook_llm.calls == len(by_agent) > 10
    assert all(x["agrees_with_playbook"] for x in by_agent)
    assert {x.get("decided_by") for x in metas} == {"master_agent", "code"}


@pytest.mark.parametrize("variant", ["missing_id", "no_human"])
def test_master_agent_follows_other_routes(playbook_llm, tmp_path, variant):
    cfg = copy.deepcopy(CFG)
    cfg["human_script"] = {"maker": [], "checker": []}
    if variant == "missing_id":
        cfg["document_store"] = [d for d in cfg["document_store"] if d["item"] != "director_ids"]
    want = Engine(copy.deepcopy(cfg), tmp_path / "rules")
    got = Engine(copy.deepcopy(cfg), tmp_path / "agent", llm=playbook_llm, live_master=True)
    quiet(want.run), quiet(got.run)
    assert got.case.status == want.case.status and got.case.answers == want.case.answers


# ------------------------------------------------------------------ guardrails reject what breaks them
def case_with(**kw):
    c = Case("X", copy.deepcopy(CFG["case"]))
    c.docs = {d["doc_id"]: {**d, "fresh": True} for d in CFG["document_store"]}
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def notify(to="priya.nair", template="MAKER_REVIEW"):
    return {"ref": "t1", "agent": A5, "inputs": {"template": template, "to": [to], "items": ["x"]}}


def proposal(decision, status, tasks=(), rules=("JUDGEMENT",)):
    return {"decision": decision, "new_status": status, "tasks": list(tasks), "rules_matched": list(rules),
            "rationale": "because"}


def errors(case, ev, p, now=NOW):
    return " | ".join(ma.check_decision(CFG, case, ev, now, p))


HIGH = Event("AGENT_RESULT", A4, {"verdict": "FAIL", "findings": [
    {"q_id": "Q2", "check": "C4", "severity": "HIGH", "issue": "UBO sum 90%", "evidence_available": True}]})


def test_g3_high_finding_cannot_go_back_to_an_agent():
    c = case_with(answers={"Q2": {}}, maker_submitted=True)
    redraft = {"ref": "t1", "agent": A3, "scope": {"questions": ["Q2"], "mode": "redraft"}}
    assert "G3" in errors(c, HIGH, proposal("DISPATCH", "IN_DRAFT", [redraft]))
    assert errors(c, HIGH, proposal("DISPATCH", "WITH_MAKER", [notify()], ["G3"])) == ""


def test_g5_only_a_checker_approval_closes():
    c = case_with()
    assert "G5" in errors(c, Event("MAKER_SUBMIT", "maker", {}), proposal("CLOSE", "CLOSED"))
    approve = Event("CHECKER_APPROVE", "checker", {"final_rating": "MEDIUM", "next_review": "2027-11-15"})
    assert "G5" in errors(c, approve, proposal("WAIT", "WITH_CHECKER"))
    assert errors(c, approve, proposal("CLOSE", "CLOSED", [notify()], ["R9"])) == ""


def test_g5_checker_sees_only_a_passing_submitted_draft():
    c = case_with(answers={"Q1": {}}, maker_submitted=False)
    passed = Event("AGENT_RESULT", A4, {"verdict": "PASS", "findings": []})
    to_checker = proposal("DISPATCH", "WITH_CHECKER", [notify("daniel.tan", "CHECKER_REVIEW")], ["R6"])
    assert "G5" in errors(c, passed, to_checker)
    c.maker_submitted = True
    assert errors(c, passed, to_checker) == ""
    assert "only the checker" in errors(c, passed, proposal("DISPATCH", "WITH_CHECKER", [notify(template="CHECKER_REVIEW")]))


def test_g2_open_screening_hit_goes_back_to_the_maker():
    c = case_with(open_items={"screening.hits": "SCREENING_HIT"}, answers={"Q6": {}})
    submit = Event("MAKER_SUBMIT", "maker", {"uploads": []})
    review = {"ref": "t1", "agent": A4, "scope": {"questions": ["Q6"]}}
    assert "G2" in errors(c, submit, proposal("DISPATCH", "AUTO_REVIEW", [review]))
    assert errors(c, submit, proposal("ESCALATE", "WITH_MAKER", [notify(template="ESCALATION")], ["G2"])) == ""


def test_g4_third_failure_must_escalate():
    c = case_with()
    failed = Event("AGENT_RESULT", A2, {"status": "FAILED", "error": "OCR timeout", "scope": {"docs": ["DOC-01"]}})
    retry = {"ref": "t1", "agent": A2, "scope": {"docs": ["DOC-01"]}}
    assert errors(c, failed, proposal("DISPATCH", "INGESTION", [retry], ["R3a"])) == ""
    c.retries[ma.retry_key(A2, {"docs": ["DOC-01"]})] = 2
    assert "G4" in errors(c, failed, proposal("DISPATCH", "INGESTION", [retry], ["R3a"]))


def test_rules_g4_counts_retries_across_new_task_ids():
    """Each retry is a new task id; the playbook must still stop after MAX_RETRIES."""
    m, case = Master(CFG), case_with()
    for i in range(1, 4):
        d = m.decide(case, Event("AGENT_RESULT", A2, {"status": "FAILED", "scope": {"docs": ["DOC-01"]}}, f"X-T0{i}"), NOW)
    assert d.decision == "ESCALATE" and "G4" in d.guardrails_triggered


def test_shape_checks():
    c = case_with()
    submit = Event("MAKER_SUBMIT", "maker", {"uploads": []})
    assert "stuck" in errors(c, submit, proposal("WAIT", "IN_DRAFT"))
    bad_doc = {"ref": "t1", "agent": A2, "scope": {"docs": ["DOC-99"]}}
    assert "scope.docs" in errors(c, submit, proposal("DISPATCH", "INGESTION", [bad_doc]))
    dep = {"ref": "t2", "agent": A3, "scope": {"questions": ["Q1"], "mode": "draft"}, "depends_on": ["t9"]}
    assert "depends_on" in errors(c, submit, proposal("DISPATCH", "IN_DRAFT", [dep]))
    assert "rules_matched" in errors(c, submit, proposal("ESCALATE", "WITH_MAKER", [notify()], ["R42"]))
    assert "user ids" in errors(c, submit, proposal("ESCALATE", "WITH_MAKER", [notify("mallory")]))


def test_code_adds_g6_copy_and_real_task_ids():
    c, late = case_with(), datetime.fromisoformat("2026-11-11T09:00:00+08:00")
    ret = Event("CHECKER_RETURN", "checker", {"returned": [{"q_id": "Q7", "comment": "x"}]})
    suggest = {"ref": "a", "agent": A3, "scope": {"questions": ["Q7"], "mode": "suggest"}}
    p = proposal("DISPATCH", "RETURNED", [suggest, {**notify(template="CHECKER_RETURN"), "ref": "b", "depends_on": ["a"]}], ["R8"])
    assert errors(c, ret, p, late) == ""
    d = ma.apply_decision(CFG, c, ret, late, p)
    assert [t.task_id for t in d.tasks] == ["X-T01", "X-T02"] and d.tasks[1].depends_on == ["X-T01"]
    assert d.tasks[1].inputs["escalate_cc"] == "grace.lim" and "G6" in d.guardrails_triggered


# ------------------------------------------------------------------ the loop with a scripted model
def tool_use(i, name, inp):
    return NS(type="tool_use", id=f"tu_{i}", name=name, input=inp)


def reply(*blocks, stop="tool_use"):
    return NS(content=list(blocks), stop_reason=stop, usage=NS(input_tokens=100, output_tokens=20))


class FakeClient:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.requests.append(copy.deepcopy(kw["messages"]))
        return self.replies.pop(0)


def test_master_agent_is_told_why_and_corrects_itself():
    c = case_with(answers={"Q2": {}}, maker_submitted=True)
    wrong = proposal("DISPATCH", "IN_DRAFT", [{"ref": "t1", "agent": A3, "scope": {"questions": ["Q2"], "mode": "redraft"}}])
    right = proposal("DISPATCH", "WITH_MAKER", [notify()], ["G3"])
    client = FakeClient([reply(tool_use(1, "get_event", {}), tool_use(2, "get_case_record", {})),
                         reply(tool_use(3, "submit_result", {"result": wrong})),
                         reply(tool_use(4, "submit_result", {"result": right}))])
    d = ma.AgenticMaster(CFG, LLM(client=client)).decide(c, HIGH, NOW)
    assert "G3" in client.requests[2][-1]["content"][0]["content"]  # the rejection named the guardrail
    assert d.new_status == "WITH_MAKER" and [t.agent for t in d.tasks] == [A5] and "G3" in d.guardrails_triggered
    assert d.meta["decided_by"] == "master_agent" and d.meta["agent_trace"]["rejected_submissions"] == 1
    assert d.meta["agrees_with_playbook"] is True


def test_no_allowed_decision_falls_back_to_the_playbook():
    c = case_with(answers={"Q2": {}}, maker_submitted=True)
    client = FakeClient([reply(NS(type="text", text="hmm"), stop="end_turn")] * 2)
    d = ma.AgenticMaster(CFG, LLM(client=client, max_turns=2)).decide(c, HIGH, NOW)
    assert d.meta["decided_by"] == "playbook_fallback" and d.rules_matched == ["G3"]


def test_waits_with_one_legal_outcome_need_no_model_call():
    c = case_with(pending=[Task("X-T05", A3, {}, {})])
    d = ma.AgenticMaster(CFG, LLM(client=FakeClient([]))).decide(c, Event("AGENT_RESULT", A2, {"status": "DONE"}), NOW)
    assert d.decision == "WAIT" and d.meta["decided_by"] == "code"


def test_parse_live():
    assert parse_live("") == (set(), False)
    assert parse_live("A3, a4") == ({A3, A4}, False)
    assert parse_live("master,A2") == ({A2}, True)
    agents, master = parse_live("all")
    assert len(agents) == 5 and master
    with pytest.raises(ValueError):
        parse_live("A9")
