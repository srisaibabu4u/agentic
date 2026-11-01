"""Run the workflow worker.

    python -m kyc.worker                  # loop forever, polling every second
    python -m kyc.worker --until-idle     # work until nothing is left, then exit
    python -m kyc.worker --live A3,A4     # those agents call Claude (needs ANTHROPIC_API_KEY)
    python -m kyc.worker --live all       # every agent and the master agent call Claude

Run several at once to scale out: tasks and cases are claimed with SKIP LOCKED.
"""
from __future__ import annotations

import argparse
import logging
import time

from . import logs
from orchestrator.models import parse_live

from .db import SessionLocal
from .services import worker

log = logging.getLogger("kyc.worker")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m kyc.worker", description=__doc__.split("\n")[0])
    ap.add_argument("--until-idle", action="store_true", help="exit when there is no work left")
    ap.add_argument("--poll", type=float, default=1.0, help="seconds to sleep when idle")
    ap.add_argument("--live", default="", help="comma list of A1..A5 and/or master, or 'all', to call Claude for those")
    ap.add_argument("--model", default=None, help="Claude model id (default: env KYC_MODEL or the llm.py default)")
    args = ap.parse_args(argv)
    logs.setup("worker")

    agents, live_master = parse_live(args.live)
    live = frozenset(agents)
    llm = None
    if live or live_master:
        from orchestrator.llm import LLM
        llm = LLM(args.model)

    name = worker.worker_name()
    log.info("worker %s started%s%s", name, f", live agents: {sorted(live)}" if live else "",
             ", master agent live" if live_master else "")
    done = 0
    while True:
        try:
            with SessionLocal() as db:
                busy = worker.step(db, name=name, llm=llm, live_agents=live, live_master=live_master)
        except Exception:  # never die silently: log it, the session rolls back, try again after a pause
            log.exception("step failed; continuing")
            busy = False
        if busy:
            done += 1
            continue
        if args.until_idle:
            log.info("idle after %d step(s); exiting", done)
            return
        time.sleep(args.poll)


if __name__ == "__main__":
    main()
