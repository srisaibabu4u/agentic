"""Routing tests: the master must pick the right route for each situation.

Run:  python -m pytest -q      (or: python tests/test_routing.py)
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

from orchestrator.engine import Engine  # noqa: E402
from orchestrator.master import Master  # noqa: E402
from orchestrator.models import A2, A3, A4, A5, Case, Event  # noqa: E402

CFG = json.loads((ROOT / "data" / "sample_case.json").read_text())
NOW = datetime.fromisoformat("2026-10-30T09:00:00+08:00")


def run(cfg, tmp):
    eng = Engine(cfg, Path(tmp))
    with redirect_stdout(io.StringIO()):
        eng.run()
    return eng


def decisions(eng):
    return [a["decision"] for a in eng.audit if a["kind"] == "master_decision"]


def test_happy_path_closes_medium(tmp_path):
    eng = run(copy.deepcopy(CFG), tmp_path)
    assert eng.case.status == "CLOSED"
    assert eng.case.final["rating"] == "MEDIUM"
    assert eng.case.final["next_review"] == "2027-11-15"
    rules = [r for d in decisions(eng) for r in d["rules_matched"]]
    for r in ("R1", "R2", "R3", "R4", "R5", "R7", "R6", "R8", "R9"):
        assert r in rules, r


def test_self_correction_loop_used_once(tmp_path):
    eng = run(copy.deepcopy(CFG), tmp_path)
    r7 = [d for d in decisions(eng) if "R7" in d["rules_matched"]]
    assert len(r7) == 1
    assert [t["agent"] for t in r7[0]["tasks"]] == [A3, A4]


def test_incremental_rework_only_touches_affected_questions(tmp_path):
    eng = run(copy.deepcopy(CFG), tmp_path)
    r5 = [d for d in decisions(eng) if "R5" in d["rules_matched"]]
    second = r5[1]["tasks"]
    assert second[0]["agent"] == A2 and second[0]["scope"]["docs"] == ["DOC-09", "DOC-10"]
    assert second[1]["scope"]["questions"] == ["Q7"]


def test_missing_identity_document_goes_to_maker_first(tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["document_store"] = [d for d in cfg["document_store"] if d["item"] != "director_ids"]
    cfg["human_script"] = {"maker": [], "checker": []}
    eng = run(cfg, tmp_path)
    d = decisions(eng)[1]
    assert d["rules_matched"] == ["R2a"]
    assert [t["agent"] for t in d["tasks"]] == [A5]
    assert eng.case.status == "WITH_MAKER"


def test_high_finding_never_self_corrects():
    m, case = Master(CFG), Case("X", CFG["case"])
    case.maker_submitted = True
    ev = Event("AGENT_RESULT", A4, {"verdict": "FAIL", "findings": [
        {"q_id": "Q2", "check": "C4", "severity": "HIGH", "issue": "UBO sum 90%", "evidence_available": True}]})
    d = m.decide(case, ev, NOW)
    assert "G3" in d.guardrails_triggered
    assert [t.agent for t in d.tasks] == [A5] and d.new_status == "WITH_MAKER"


def test_retry_limit_escalates():
    m, case = Master(CFG), Case("X", CFG["case"])
    ev = Event("AGENT_RESULT", A2, {"status": "FAILED", "error": "OCR timeout"}, task_id="X-T02")
    for _ in range(2):
        assert m.decide(case, ev, NOW).decision == "DISPATCH"
    d = m.decide(case, ev, NOW)
    assert d.decision == "ESCALATE" and "G4" in d.guardrails_triggered


def test_maker_cannot_submit_with_open_screening_hit():
    m, case = Master(CFG), Case("X", CFG["case"])
    case.open_items["screening.hits"] = "SCREENING_HIT"
    d = m.decide(case, Event("MAKER_SUBMIT", "maker", {"uploads": []}), NOW)
    assert d.decision == "ESCALATE" and "G2" in d.guardrails_triggered


def test_close_to_due_date_copies_team_lead():
    m, case = Master(CFG), Case("X", CFG["case"])
    late = datetime.fromisoformat("2026-11-11T09:00:00+08:00")
    d = m.decide(case, Event("CHECKER_RETURN", "checker", {"returned": [{"q_id": "Q7", "comment": "x"}]}), late)
    notify = [t for t in d.tasks if t.agent == A5][0]
    assert notify.inputs["escalate_cc"] == "grace.lim" and "G6" in d.guardrails_triggered


def test_maker_equals_checker_blocked():
    cfg = copy.deepcopy(CFG)
    cfg["case"]["checker"] = cfg["case"]["maker"]
    m, case = Master(cfg), Case("X", cfg["case"])
    d = m.decide(case, Event("CHECKER_APPROVE", "checker", {"final_rating": "MEDIUM", "next_review": "2027-11-15"}), NOW)
    assert d.decision == "ESCALATE" and d.new_status != "CLOSED"


if __name__ == "__main__":
    import tempfile
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            args = [Path(tempfile.mkdtemp())] if fn.__code__.co_argcount else []
            fn(*args)
            n += 1
            print("ok ", name)
    print(f"{n} passed")
