"""The agents must handle any generated client, not only Halcyon Marine.

Each generated client is turned into a prototype-style case config and run
through the prototype Engine with no scripted humans. It must run until it
legitimately needs the maker, via the route its scenario implies. Halcyon must
still produce exactly the committed out/ files.
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orchestrator.engine import Engine  # noqa: E402

from kyc.seed.generator import SCENARIOS, Generator  # noqa: E402
from kyc.seed.reference import USERS, country_risk, load_prototype  # noqa: E402

PROTO = load_prototype()
PEOPLE = {u["username"]: {"id": u["username"], "name": u["full_name"], "role": u["title"]} for u in USERS}

# scenario -> rule of the last routing decision before the case waits on the maker
EXPECTED_STOP = {
    "happy": "R6",              # review passed; maker still has to submit
    "near_due": "R6",
    "stale_fs": "R7b",          # review finds stale FS that only the maker can fix
    "missing_id": "R2a",        # identity document missing: maker first, no drafting
    "screening_hit": "R4",      # open hit needs a disposition
    "obscured_register": "R4",  # low-confidence shareholding needs confirming
    "ubo_mismatch": "G3",       # HIGH finding: UBO % do not sum to 100
}


def case_config(spec: dict, human_script: dict | None = None) -> dict:
    c, k = spec["client"], spec["case"]
    q = dict(PROTO["questionnaire"], country_risk=country_risk(PROTO))
    return {
        "case": {"case_id": k["case_ref"], "client_id": c["cif"], "legal_name": c["legal_name"],
                 "short_name": c["short_name"], "uen": c["uen"], "segment": c["segment"],
                 "prior_rating": c["prior_rating"], "prior_assessment_date": c["prior_assessment_date"].isoformat(),
                 "due_date": k["due_date"].isoformat(), "start_time": k["opened_at"].isoformat(),
                 "maker": PEOPLE["priya.nair"], "checker": PEOPLE["daniel.tan"], "team_lead": PEOPLE["grace.lim"],
                 "case_url": f"https://kyc.example.internal/cases/{k['case_ref']}"},
        "checklist": PROTO["checklist"], "document_store": spec["documents"],
        "human_script": human_script or {"maker": [], "checker": []},
        "dependency_map": PROTO["dependency_map"], "questionnaire": q,
    }


def last_route(engine: Engine) -> dict:
    return [r for r in engine.audit if r["kind"] == "master_decision" and r["decision"]["decision"] != "WAIT"][-1]["decision"]


@pytest.fixture(scope="module")
def specs():
    return Generator(seed=42, as_of=__import__("datetime").date(2026, 10, 31)).clients(3 * len(SCENARIOS))


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_generated_case_runs_to_maker(specs, scenario, tmp_path, capsys):
    for spec in [s for s in specs if s["case"]["scenario"] == scenario]:
        e = Engine(case_config(spec), tmp_path)
        e.start()
        stop = e.advance(use_script=False)
        route = last_route(e)
        assert stop == "WAITING_ON_MAKER", (spec["case"]["case_ref"], stop)
        assert EXPECTED_STOP[scenario] in route["rules_matched"], (spec["case"]["case_ref"], route)
        assert ("G6" in route["guardrails_triggered"]) == (scenario == "near_due")
        if scenario != "missing_id":
            assert sorted(e.case.answers) == [f"Q{i}" for i in range(1, 9)]


def test_happy_case_closes_with_maker_and_checker(specs, tmp_path, capsys):
    spec = next(s for s in specs if s["case"]["scenario"] == "happy")
    t0 = spec["case"]["opened_at"]
    script = {"maker": [{"at": (t0 + timedelta(hours=3)).isoformat(), "event": "MAKER_SUBMIT",
                         "uploads": [], "field_edits": {}, "dispositions": {}}],
              "checker": [{"at": (t0 + timedelta(days=1)).isoformat(), "event": "CHECKER_APPROVE",
                           "final_rating": "MEDIUM", "comment": "ok"}]}
    e = Engine(case_config(spec, script), tmp_path)
    e.start()
    assert e.advance(use_script=True) == "CLOSED"
    assert e.case.rating["band"] in ("LOW", "MEDIUM", "HIGH")


def test_halcyon_output_unchanged(tmp_path, capsys):
    Engine(PROTO, tmp_path).run()
    for name in ("final_case.json", "outbox.json"):
        assert json.loads((tmp_path / name).read_text()) == json.loads((ROOT / "out" / name).read_text()), name
    got = [json.loads(line) for line in (tmp_path / "audit_log.jsonl").read_text().splitlines()]
    want = [json.loads(line) for line in (ROOT / "out" / "audit_log.jsonl").read_text().splitlines()]
    assert got == want


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_live_result_checks_accept_offline_results(specs, scenario, tmp_path, capsys, monkeypatch):
    """The checks a live agent's submission must pass hold for every deterministic result, in every scenario."""
    from orchestrator import agents as ag
    real, errors = ag.BaseAgent.run, []

    def checked(self, task, case, now):
        res = real(self, task, case, now)
        errors.extend(f"{task.task_id}: {e}" for e in self.validate(task, case, now, json.loads(json.dumps(res))))
        return res

    monkeypatch.setattr(ag.BaseAgent, "run", checked)
    for spec in [s for s in specs if s["case"]["scenario"] == scenario]:
        e = Engine(case_config(spec), tmp_path)
        e.start()
        e.advance(use_script=False)
    assert errors == []
