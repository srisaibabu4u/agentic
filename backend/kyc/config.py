"""Settings read from the environment (or a .env file at the repo root)."""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


def _load_dotenv() -> None:
    env = REPO_ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

DATABASE_URL = os.getenv("KYC_DATABASE_URL", "postgresql+psycopg://kyc:kyc@localhost:5432/kyc")
GENERATED_DIR = Path(os.getenv("KYC_GENERATED_DIR", BACKEND_DIR / "data" / "generated"))
UPLOADS_DIR = Path(os.getenv("KYC_UPLOADS_DIR", BACKEND_DIR / "data" / "uploads"))
# Business clock for case times shown to people and passed to the agents (Singapore, no DST).
BUSINESS_TZ_HOURS = int(os.getenv("KYC_TZ_HOURS", "8"))
PROTOTYPE_CASE = REPO_ROOT / "data" / "sample_case.json"

# ---- login (Phase 4). Tokens are HS256 JWTs; set KYC_JWT_SECRET to a long random value outside development.
JWT_SECRET = os.getenv("KYC_JWT_SECRET", "")
TOKEN_HOURS = float(os.getenv("KYC_TOKEN_HOURS", "8"))
# Mark the session cookie Secure (HTTPS only). On by default; turn off only for plain-http local development.
COOKIE_SECURE = os.getenv("KYC_COOKIE_SECURE", "true").lower() == "true"
