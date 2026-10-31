"""The generated documents must be readable by the existing agents, and each scenario must
produce the condition it is named after. No database needed."""
from __future__ import annotations

import sys
from datetime import date, datetime, time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orchestrator.agents import DiscoveryAgent, IngestionAgent  # noqa: E402
from orchestrator.master import business_days_between  # noqa: E402
from orchestrator.models import Case, Task  # noqa: E402

from kyc.seed.generator import SCENARIOS, Generator, halcyon  # noqa: E402
from kyc.seed.reference import load_prototype  # noqa: E402

AS_OF = date(2026, 10, 31)
PROTO = load_prototype()


def discover_and_ingest(spec: dict) -> tuple[dict, dict, datetime]:
    cfg = {"document_store": spec["documents"], "checklist": PROTO["checklist"]}
    case = Case(spec["case"]["case_ref"], meta={"due_date": spec["case"]["due_date"].isoformat()})
    now = spec["case"]["opened_at"]
    manifest = DiscoveryAgent(cfg).offline(Task("T1", "A1", {}), case, now)
    case.docs = {d["doc_id"]: d for d in spec["documents"]}
    found = [f["doc_id"] for f in manifest["found"]]
    ingest = IngestionAgent(cfg).offline(Task("T2", "A2", {"docs": found}), case, now)
    return manifest, ingest, now


@pytest.fixture(scope="module")
def specs() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for s in Generator(seed=42, as_of=AS_OF).clients(4 * len(SCENARIOS)):
        out.setdefault(s["case"]["scenario"], []).append(s)
    return out


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_scenario_condition(specs, scenario):
    for spec in specs[scenario]:
        manifest, ingest, now = discover_and_ingest(spec)
        missing = {m["checklist_item"]: m for m in manifest["missing"]}
        flags = {f["code"] for f in ingest["flags"]}
        fields = ingest["fields"]
        u1, u2 = fields["ubo.1"]["value"]["pct"], fields["ubo.2"]["value"]["pct"]
        due_soon = business_days_between(now, spec["case"]["due_date"].isoformat()) < 5

        assert ingest["status"] == "DONE"
        assert fields["entity.uen"]["value"] == spec["client"]["uen"]
        assert len(fields["directors"]["value"]) == 2

        if scenario == "stale_fs":
            assert list(missing) == ["audited_fs"] and not missing["audited_fs"]["identity_critical"]
        elif scenario == "missing_id":
            assert list(missing) == ["director_ids"] and missing["director_ids"]["identity_critical"]
        else:
            assert not missing, missing

        assert ("SCREENING_HIT" in flags) == (scenario == "screening_hit")
        assert ("LOW_CONF_FIELD" in flags) == (scenario == "obscured_register")
        if scenario == "ubo_mismatch":
            assert u1 + u2 < 100
        else:
            assert u1 + u2 == 100
        assert due_soon == (scenario == "near_due")


def test_latest_fs_is_picked_and_parsed(specs):
    spec = specs["happy"][0]
    _, ingest, _ = discover_and_ingest(spec)
    assert [k for k in ingest["fields"] if k.startswith("turnover.")] == ["turnover.fy2025"]


def test_same_seed_same_data():
    a = Generator(seed=7, as_of=AS_OF).clients(5)
    b = Generator(seed=7, as_of=AS_OF).clients(5)
    assert a == b
    assert len({s["client"]["uen"] for s in a}) == 5


def test_halcyon_matches_prototype():
    spec = halcyon(PROTO)
    manifest, ingest, _ = discover_and_ingest(spec)
    assert [m["checklist_item"] for m in manifest["missing"]] == ["audited_fs"]
    assert {f["code"] for f in ingest["flags"]} == {"SCREENING_HIT", "LOW_CONF_FIELD"}
    assert spec["case"]["opened_at"] == datetime.combine(date(2026, 10, 30), time(9, 0), spec["case"]["opened_at"].tzinfo)
