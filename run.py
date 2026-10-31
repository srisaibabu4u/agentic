"""Run the KYC renewal workflow end to end on the dummy case.

    python run.py                       # offline, deterministic, no API key
    python run.py -v                    # also print message bodies and raw results
    python run.py --live A3,A4          # those agents call Claude (needs ANTHROPIC_API_KEY)
    python run.py --live all --shadow   # all agents live + LLM master shadows the rules
"""
import argparse
import json
from pathlib import Path

from orchestrator.engine import Engine

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", default=str(ROOT / "data" / "sample_case.json"))
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--live", default="", help="comma list of A1..A5 or 'all' to call Claude for those agents")
    ap.add_argument("--shadow", action="store_true", help="LLM master re-decides each step and is compared to the rules")
    ap.add_argument("--model", default=None, help="Claude model id (default env KYC_MODEL or claude-sonnet-5-5)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    cfg = json.loads(Path(a.case).read_text())
    ids = {"A1": "A1_DISCOVERY", "A2": "A2_INGEST", "A3": "A3_DRAFT", "A4": "A4_REVIEW", "A5": "A5_NOTIFY"}
    live = set(ids.values()) if a.live == "all" else {ids[x.strip().upper()] for x in a.live.split(",") if x.strip()}
    llm = None
    if live or a.shadow:
        from orchestrator.llm import LLM
        llm = LLM(a.model)
    Engine(cfg, Path(a.out), llm=llm, live_agents=live, shadow_master=a.shadow, verbose=a.verbose).run()


if __name__ == "__main__":
    main()
