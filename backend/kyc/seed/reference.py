"""Reference data: the prototype's checklist, questionnaire and dependency map, plus users and countries."""
from __future__ import annotations

import json

from ..config import PROTOTYPE_CASE

DUMMY_PASSWORD = "kyc-demo-123"  # every seeded user; dummy data only

USERS = [
    {"username": "priya.nair", "full_name": "Priya Nair", "role": "maker", "title": "Maker (RM)"},
    {"username": "aaron.koh", "full_name": "Aaron Koh", "role": "maker", "title": "Maker (RM)"},
    {"username": "daniel.tan", "full_name": "Daniel Tan", "role": "checker", "title": "Checker (Compliance)"},
    {"username": "siti.rahman", "full_name": "Siti Rahman", "role": "checker", "title": "Checker (Compliance)"},
    {"username": "grace.lim", "full_name": "Grace Lim", "role": "admin", "title": "Team lead"},
]
EMAIL_DOMAIN = "kyc.example"
TEAM_LEAD = "grace.lim"

# The prototype's list (SG, MY, ID, AE, BVI) extended for the generated clients.
COUNTRIES = {
    "SG": ("Singapore", 1), "MY": ("Malaysia", 2), "ID": ("Indonesia", 3), "AE": ("United Arab Emirates", 3),
    "BVI": ("British Virgin Islands", 4), "KY": ("Cayman Islands", 4), "HK": ("Hong Kong", 2),
    "CN": ("China", 3), "IN": ("India", 3), "TH": ("Thailand", 2), "VN": ("Vietnam", 3),
    "PH": ("Philippines", 3), "AU": ("Australia", 1), "GB": ("United Kingdom", 1), "JP": ("Japan", 1),
    "US": ("United States", 1), "DE": ("Germany", 1), "MM": ("Myanmar", 5), "PK": ("Pakistan", 4),
}


def load_prototype() -> dict:
    return json.loads(PROTOTYPE_CASE.read_text(encoding="utf-8"))


def country_risk(proto: dict) -> dict[str, int]:
    """Prototype levels win where both define a country."""
    return {**{k: lvl for k, (_, lvl) in COUNTRIES.items()}, **proto["questionnaire"]["country_risk"]}
