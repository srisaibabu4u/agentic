"""The workflow map: case statuses and the routes the master can take between them.

Derived from orchestrator/master.py. tests/test_worker.py checks that every
status change the master makes in a full run is on this map with a matching
rule, so the map cannot silently drift from the code.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .deps import current_user

# id, label, who acts while the case is in this status
NODES = [
    ("NEW", "New", "scheduler"),
    ("DISCOVERY", "Discovery", "agents"),
    ("INGESTION", "Ingestion", "agents"),
    ("IN_DRAFT", "Drafting", "agents"),
    ("AUTO_REVIEW", "Auto review", "agents"),
    ("WITH_MAKER", "With maker", "maker"),
    ("WITH_CHECKER", "With checker", "checker"),
    ("RETURNED", "Returned", "maker"),
    ("CLOSED", "Closed", "done"),
]

ESCALATE = ["R3a", "G4"]  # an agent failed MAX_RETRIES times: hand the case to the maker

# source, target, rules that produce this change, what it means.
# Some dispatches are chains (R5: ingest -> redraft; R7: redraft -> review). The case keeps the status of
# the chain's first task until the chain ends, so the next decision can start from that earlier status:
# hence the INGESTION -> AUTO_REVIEW/WITH_MAKER and IN_DRAFT -> WITH_CHECKER routes below.
EDGES = [
    ("NEW", "DISCOVERY", ["R1"], "Renewal due: find the documents"),
    ("DISCOVERY", "INGESTION", ["R2"], "Documents found: ingest them, request any missing in parallel"),
    ("DISCOVERY", "WITH_MAKER", ["R2a", *ESCALATE], "Identity document missing, or too little found"),
    ("INGESTION", "IN_DRAFT", ["R3"], "Fields extracted: draft every answer"),
    ("INGESTION", "AUTO_REVIEW", ["R4b"], "Ingest-and-redraft chain done, no open items: review"),
    ("INGESTION", "WITH_MAKER", ["R4", *ESCALATE], "Open items after the redraft, or ingestion keeps failing"),
    ("IN_DRAFT", "AUTO_REVIEW", ["R4b"], "No open items: run the automated review"),
    ("IN_DRAFT", "WITH_MAKER", ["R4", "R6", "R7b", "G3", *ESCALATE],
     "Open items need a person (G1 low confidence, G2 screening hit), or the redraft-and-review chain needs the maker"),
    ("IN_DRAFT", "WITH_CHECKER", ["R6"], "Redraft-and-review chain passed and the maker has submitted"),
    ("AUTO_REVIEW", "IN_DRAFT", ["R7"], "Fixable findings: redraft, then review again"),
    ("AUTO_REVIEW", "WITH_MAKER", ["R6", "R7b", "G3", *ESCALATE],
     "Passed but not yet submitted, or a finding only the maker can fix"),
    ("AUTO_REVIEW", "WITH_CHECKER", ["R6"], "Passed and submitted: only a checker can approve (G5)"),
    ("WITH_MAKER", "INGESTION", ["R5"], "Maker uploaded documents: ingest only those"),
    ("WITH_MAKER", "IN_DRAFT", ["R5"], "Maker changed values: redraft the affected answers"),
    ("WITH_MAKER", "AUTO_REVIEW", ["R5c"], "Maker submitted without changes"),
    ("WITH_CHECKER", "RETURNED", ["R8"], "Checker returned questions to the maker"),
    ("RETURNED", "INGESTION", ["R5"], "Maker uploaded the requested evidence"),
    ("RETURNED", "IN_DRAFT", ["R5"], "Maker changed values"),
    ("RETURNED", "AUTO_REVIEW", ["R5c"], "Maker resubmitted without changes"),
    ("WITH_CHECKER", "CLOSED", ["R9"], "Checker approved: close and schedule the next review"),
]

router = APIRouter(tags=["meta"], dependencies=[Depends(current_user)])


@router.get("/workflow")
def workflow() -> dict:
    return {"nodes": [{"id": i, "label": label, "actor": actor} for i, label, actor in NODES],
            "edges": [{"id": f"{s}->{t}", "source": s, "target": t, "rules": rules, "description": d}
                      for s, t, rules, d in EDGES]}
