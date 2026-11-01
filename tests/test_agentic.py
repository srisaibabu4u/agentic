"""Agentic (live) agents, tested without an API key.

A scripted fake client stands in for Claude, so these tests run the real
tool-use loop, the real tools and the real result checks for every task the
sample case produces.

Run:  python -m pytest -q tests/test_agentic.py
"""
import copy
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator import agents as ag  # noqa: E402
from orchestrator.engine import Engine  # noqa: E402
from orchestrator.llm import LLM  # noqa: E402

CFG = json.loads((ROOT / "data" / "sample_case.json").read_text())
FIRST_TOOL = {"A1_DISCOVERY": "get_checklist", "A2_INGEST": "list_documents", "A3_DRAFT": "get_questions",
              "A4_REVIEW": "get_answers_in_scope", "A5_NOTIFY": "get_recipients"}


@pytest.fixture(scope="module")
def calls(tmp_path_factory):
    """(agent, task, case, now, offline result) for every agent task of the sample case."""
    seen, real = [], ag.BaseAgent.run

    def spy(self, task, case, now):
        before = copy.deepcopy(case)
        res = real(self, task, case, now)
        seen.append((self, copy.deepcopy(task), before, now, copy.deepcopy(res)))
        return res

    ag.BaseAgent.run = spy
    try:
        with redirect_stdout(io.StringIO()):
            Engine(copy.deepcopy(CFG), tmp_path_factory.mktemp("out")).run()
    finally:
        ag.BaseAgent.run = real
    return seen


# ------------------------------------------------------------------ fake Claude
def tool_use(i, name, inp):
    return NS(type="tool_use", id=f"tu_{i}", name=name, input=inp)


def reply(*blocks, stop="tool_use"):
    return NS(content=list(blocks), stop_reason=stop, usage=NS(input_tokens=100, output_tokens=20))


class FakeClient:
    """Replays scripted replies and records what each request contained."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.requests.append(copy.deepcopy(kw["messages"]))
        self.kw = kw
        return self.replies.pop(0)


def live(agent, client, **kw):
    return type(agent)(agent.cfg, LLM(client=client, model="claude-sonnet-5-5", **kw))


# ------------------------------------------------------------------ tests
def test_every_agent_runs_the_loop_and_self_corrects(calls):
    assert {c[0].agent_id for c in calls} == set(FIRST_TOOL)
    for agent, task, case, now, offline in calls:
        client = FakeClient([
            reply(tool_use(1, FIRST_TOOL[agent.agent_id], {})),
            reply(tool_use(2, "submit_result", {"result": {}})),                # wrong: rejected
            reply(tool_use(3, "submit_result", {"result": copy.deepcopy(offline), "self_check": "ok"})),
        ])
        res = live(agent, client).run(task, copy.deepcopy(case), now)
        trace = res.pop("agent_trace")
        assert res.get("status") != "FAILED", (task.task_id, res)
        assert trace["turns"] == 3 and trace["rejected_submissions"] == 1, task.task_id
        assert [s["tool"] for s in trace["steps"]] == [FIRST_TOOL[agent.agent_id], "submit_result", "submit_result"]
        # the rejection is sent back to the agent as a tool error it can act on
        last = client.requests[-1][-1]["content"][0]
        assert last["is_error"] and "missing keys" in last["content"]
        assert client.kw["fallbacks"] == "default" and "temperature" not in client.kw


def test_validators_accept_what_the_deterministic_agents_produce(calls):
    for agent, task, case, now, offline in calls:
        assert agent.validate(task, copy.deepcopy(case), now, copy.deepcopy(offline)) == [], task.task_id


def test_validators_catch_ungrounded_results(calls):
    by = {}
    for c in calls:
        by.setdefault(c[0].agent_id, c)
    a1, t1, c1, n1, r1 = by["A1_DISCOVERY"]
    bad = copy.deepcopy(r1)
    bad["found"][0]["doc_id"] = "DOC-99"
    assert any("does not exist" in e for e in a1.validate(t1, c1, n1, bad))

    a2, t2, c2, n2, r2 = by["A2_INGEST"]
    bad = copy.deepcopy(r2)
    fid = next(iter(bad["fields"]))
    bad["fields"][fid]["raw"] = "Something the document never says"
    assert any("raw text is not found" in e for e in a2.validate(t2, c2, n2, bad))

    a5, t5, c5, n5, r5 = by["A5_NOTIFY"]
    bad = copy.deepcopy(r5)
    bad["sent"][0]["body"] = "Hi"
    assert a5.validate(t5, c5, n5, bad)


def test_tools_only_read(calls):
    for agent, task, case, now, _ in calls:
        snap = copy.deepcopy(case)
        for t in agent.tools(task, case, now):
            if not t.required:
                t.fn()
        assert case.__dict__ == snap.__dict__, task.task_id


def test_no_submission_within_budget_fails_for_the_retry_guardrail(calls):
    agent, task, case, now, _ = calls[0]
    client = FakeClient([reply(NS(type="text", text="thinking about it"), stop="end_turn")] * 3)
    res = live(agent, client, max_turns=3).run(task, case, now)
    assert res["status"] == "FAILED" and "3 turns" in res["error"]
    assert "not submitted" in client.requests[-1][-1]["content"]


def test_tool_errors_are_returned_not_raised(calls):
    agent, task, case, now, offline = calls[0]
    client = FakeClient([
        reply(tool_use(1, "check_freshness", {"doc_id": "NOPE"}), tool_use(2, "no_such_tool", {})),
        reply(tool_use(3, "submit_result", {"result": offline})),
    ])
    res = live(agent, client).run(task, case, now)
    errs = [r for r in client.requests[1][-1]["content"] if r["is_error"]]
    assert len(errs) == 2  # both results of the turn come back together, both as errors
    assert res["status"] == offline["status"] and res["agent_trace"]["steps"][0]["ok"] is False
