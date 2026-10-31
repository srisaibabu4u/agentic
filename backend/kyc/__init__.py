"""KYC client risk assessment: production backend."""
import sys
from pathlib import Path

# The master and agents live in the prototype package at the repo root (orchestrator/), shared by both.
_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
