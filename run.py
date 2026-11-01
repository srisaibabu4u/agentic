"""Run the KYC renewal workflow end to end on the dummy case.

    python run.py                       # offline, deterministic, no API key
    python run.py -v                    # also print message bodies and raw results
    python run.py --live A3,A4          # those agents call Claude (needs ANTHROPIC_API_KEY)
    python run.py --live master         # the master agent decides every step; agents stay offline
    python run.py --live all            # every agent and the master call Claude
"""
import argparse
import json
from pathlib import Path

from orchestrator.engine import Engine
from orchestrator.models import parse_live

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", default=str(ROOT / "data" / "sample_case.json"))
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--live", default="", help="comma list of A1..A5 and/or master, or 'all', to call Claude for those")
    ap.add_argument("--model", default=None, help="Claude model id (default env KYC_MODEL or claude-sonnet-5-5)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    cfg = json.loads(Path(a.case).read_text())
    live, live_master = parse_live(a.live)
    llm = None
    if live or live_master:
        from orchestrator.llm import LLM
        llm = LLM(a.model)
    Engine(cfg, Path(a.out), llm=llm, live_agents=live, live_master=live_master, verbose=a.verbose).run()


if __name__ == "__main__":
    main()
