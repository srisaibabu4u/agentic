"""Builds dummy clients, related parties, documents and renewal cases.

Pure functions, no database: each client is a plain dict, and the document
text uses exactly the layout the prototype agents parse (orchestrator/agents.py
EXTRACTORS), so a generated case can run through the same workflow as the
Halcyon Marine sample. The loader in __main__.py writes these dicts to Postgres.

Every client gets one scenario, which decides what is wrong with its file:

  happy              everything fresh and consistent
  stale_fs           latest audited FS is more than 18 months before the due date
  missing_id         no director identity documents on file (identity-critical)
  screening_hit      a director has a potential screening match (false positive)
  obscured_register  a shareholding is partly unreadable, so extraction confidence is low
  ubo_mismatch       shareholder percentages do not add up to 100%
  near_due           due in fewer than 5 business days, so the team lead is copied
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone

from faker import Faker

SCENARIOS = ["happy", "stale_fs", "missing_id", "screening_hit", "obscured_register", "ubo_mismatch", "near_due"]
SGT = timezone(timedelta(hours=8))

# (principal activity, SSIC code, name word)
INDUSTRIES = [
    ("Freight forwarding and ship chartering", "52291", "Marine Logistics"),
    ("Wholesale of electronic components", "46521", "Electronics Trading"),
    ("Software development and IT consultancy", "62011", "Technologies"),
    ("Wholesale of refined petroleum products", "46610", "Energy Trading"),
    ("Construction of commercial buildings", "41002", "Builders"),
    ("Restaurants and catering services", "56111", "F&B Holdings"),
    ("Wholesale of jewellery and precious metals", "46495", "Gems & Jewellery"),
    ("Manufacture of plastic packaging", "22220", "Packaging Industries"),
    ("Distribution of medical devices", "46442", "Medical Supplies"),
    ("Wholesale of agricultural commodities", "46100", "Agri Commodities"),
]
NAME_WORDS = ["Meridian", "Orchid", "Straits", "Lionsgate", "Banyan", "Kestrel", "Sentosa", "Harbourfront",
              "Evergreen", "Jade", "Pinnacle", "Tanjong", "Coral", "Summit", "Raffles", "Mariner", "Nova", "Cendana",
              "Aurora", "Keppel", "Bukit", "Sapphire", "Tembusu", "Lumen", "Arcadia", "Vista", "Peregrine"]
SG_SURNAMES = ["Tan", "Lim", "Lee", "Ng", "Ong", "Wong", "Goh", "Chua", "Chan", "Koh", "Teo", "Ang", "Yeo", "Tay", "Sim"]
SG_GIVEN = ["Wei Jie", "Hui Min", "Jia Hao", "Mei Ling", "Kok Wai", "Siew Lan", "Zhi Wei", "Li Ting", "Boon Keng", "Xin Yi"]
MY_NAMES = ["Ahmad Faizal bin Ismail", "Nurul Aina binti Hassan", "Mohd Hafiz bin Razak", "Siti Aminah binti Yusof",
            "Lee Chong Ming", "Kavitha a/p Subramaniam", "Wong Kar Leong", "Farah Nadia binti Karim"]
FOREIGN_LOCALES = {"IN": "en_IN", "MY": None, "GB": "en_GB", "AU": "en_AU", "ID": "id_ID", "US": "en_US"}
OFFSHORE = [("BVI", "Ltd (BVI)"), ("KY", "Ltd (Cayman)"), ("HK", "Limited (HK)")]
TRADE_PARTNERS = ["MY", "ID", "TH", "VN", "CN", "HK", "IN", "AE", "AU", "JP", "PH", "GB", "US", "DE"]
SOW = ["Founder equity built from operating profits since {y}",
       "Accumulated salary and bonuses as a senior executive, {y} to date",
       "Inheritance from family business, received {y}",
       "Proceeds from sale of a prior company in {y}"]
WATCHLISTS = ["WorldCheck", "Dow Jones RC", "LexisNexis Bridger"]


def add_business_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def latest_fy_end(as_of: date) -> date:
    """Most recent 31 Dec whose audited statements would normally be out (4 months after FYE)."""
    y = as_of.year - 1 if as_of.month > 4 else as_of.year - 2
    return date(y, 12, 31)


class Generator:
    def __init__(self, seed: int = 42, as_of: date | None = None):
        self.rng = random.Random(seed)
        self.as_of = as_of or date.today()
        self.fakers = {loc: Faker(loc) for loc in sorted(filter(None, set(FOREIGN_LOCALES.values())))}
        for i, f in enumerate(self.fakers.values()):
            f.seed_instance(seed + i)
        self._uens: set[str] = set()
        self._names: set[str] = set()
        self._cifs: set[str] = set()

    # ------------------------------------------------------------------ people / ids
    def person(self, nationality: str) -> str:
        if nationality == "SG":
            return f"{self.rng.choice(SG_SURNAMES)} {self.rng.choice(SG_GIVEN)}"
        if nationality == "MY":
            return self.rng.choice(MY_NAMES)
        f =self.fakers[FOREIGN_LOCALES[nationality]]
        return f"{f.first_name()} {f.last_name()}"

    def dob(self, lo: int = 30, hi: int = 70) -> date:
        return self.as_of - timedelta(days=self.rng.randint(lo * 365, hi * 365))

    def uen(self, year: int) -> str:
        while True:
            u = f"{year}{self.rng.randint(10000, 99999)}{self.rng.choice('ABCDEGHKMNRWXZ')}"
            if u not in self._uens:
                self._uens.add(u)
                return u

    def cif(self) -> str:
        while True:
            c = f"CIF-{self.rng.randint(100000, 999999)}"
            if c not in self._cifs:
                self._cifs.add(c)
                return c

    def company_name(self, word: str) -> str:
        while True:
            n = f"{self.rng.choice(NAME_WORDS)} {word}"
            if n not in self._names:
                self._names.add(n)
                return n

    # ------------------------------------------------------------------ one client
    def client(self, scenario: str, case_ref: str) -> dict:
        r, as_of = self.rng, self.as_of
        activity, ssic, word = r.choice(INDUSTRIES)
        short = self.company_name(word)
        inc_year = r.randint(2003, 2020)
        legal = f"{short} Pte Ltd"

        due = add_business_days(as_of, r.randint(1, 4)) if scenario == "near_due" else as_of + timedelta(days=r.randint(10, 75))
        prior = due.replace(year=due.year - 1)

        # Directors: one Singaporean founder, one foreign co-director.
        d1_nat, d2_nat = "SG", r.choice(list(FOREIGN_LOCALES))
        d1 = {"key": "d1", "party_type": "PERSON", "name": self.person(d1_nat), "nationality": d1_nat, "dob": self.dob(),
              "is_director": True, "appointed_on": date(inc_year, r.randint(1, 12), r.randint(1, 28)),
              "id_document": "NRIC"}
        d2 = {"key": "d2", "party_type": "PERSON", "name": self.person(d2_nat), "nationality": d2_nat, "dob": self.dob(),
              "is_director": True, "appointed_on": date(r.randint(inc_year, 2022), r.randint(1, 12), r.randint(1, 28)),
              "id_document": "Passport", "passport_expiry": due + timedelta(days=r.randint(400, 3000))}
        parties = [d1, d2]

        # Shareholders: the founder plus either an offshore/HK holding company or the co-director.
        total_shares = r.choice([100_000, 500_000, 1_000_000, 2_000_000])
        p1 = r.randint(51, 85)
        p2 = 100 - p1
        if scenario == "ubo_mismatch":
            p2 -= r.choice([5, 10, 15])
        d1.update(is_shareholder=True, is_ubo=True, ownership_pct=p1, shares_held=total_shares * p1 // 100,
                  source_of_wealth=r.choice(SOW).format(y=inc_year))
        if r.random() < 0.6:
            cc, suffix = r.choice(OFFSHORE)
            holdco = {"key": "s2", "party_type": "ENTITY", "name": f"{r.choice(NAME_WORDS)} Holdings {suffix}",
                      "nationality": cc, "is_shareholder": True, "ownership_pct": p2,
                      "shares_held": total_shares * p2 // 100}
            ubo_nat = r.choice(["SG", "MY", "IN"])
            parties += [holdco, {"key": "u2", "parent_key": "s2", "party_type": "PERSON", "name": self.person(ubo_nat),
                                 "nationality": ubo_nat, "dob": self.dob(), "is_ubo": True, "ownership_pct": 100,
                                 "source_of_wealth": r.choice(SOW).format(y=r.randint(2000, 2021))}]
            sh2 = holdco
        else:
            d2.update(is_shareholder=True, is_ubo=True, ownership_pct=p2, shares_held=total_shares * p2 // 100,
                      source_of_wealth=r.choice(SOW).format(y=r.randint(2000, 2021)))
            sh2 = d2

        trade = ["SG"] + r.sample(TRADE_PARTNERS, r.randint(1, 3))
        new_lane = r.choice(TRADE_PARTNERS) if r.random() < 0.4 else None
        turnover = r.randint(20, 600) * 100_000
        client = {"cif": self.cif(), "legal_name": legal, "short_name": short, "uen": self.uen(inc_year),
                  "segment": "Corporate SG", "incorporation_country": "SG", "principal_activity": activity,
                  "ssic_code": ssic, "incorporated_on": date(inc_year, 1, 1).replace(month=r.randint(1, 12)),
                  "prior_rating": r.choice(["LOW", "MEDIUM", "MEDIUM", "HIGH"]), "prior_assessment_date": prior,
                  "next_review_date": due}
        docs = self.documents(scenario, client, parties, d1, d2, sh2, total_shares, trade, new_lane, turnover, due, prior)
        case = {"case_ref": case_ref, "due_date": due, "scenario": scenario,
                "opened_at": datetime.combine(as_of, time(9, 0), SGT)}
        return {"client": client, "parties": parties, "documents": docs, "case": case}

    # ------------------------------------------------------------------ documents
    def documents(self, scenario, c, parties, d1, d2, sh2, total, trade, new_lane, turnover, due, prior) -> list[dict]:
        r, as_of = self.rng, self.as_of
        docs: list[dict] = []

        def add(item, source, dtype, ddate, text, **extra):
            docs.append({"doc_id": f"DOC-{len(docs) + 1:02d}", "source": source, "item": item, "type": dtype,
                         "date": ddate.isoformat(), **{k: v.isoformat() for k, v in extra.items()}, "text": text})

        directors = [d1, d2]
        add("registry_extract", "ACRA", "ACRA BizFile", as_of - timedelta(days=r.randint(0, 10)),
            f"[p1]\nName: {c['legal_name']}\nUEN: {c['uen']}\n"
            f"Principal activity: {c['principal_activity']} (SSIC {c['ssic_code']})\n[p2]\n"
            + "\n".join(f"Director: {d['name']} | Nationality: {d['nationality']} | Appointed {d['appointed_on']}"
                        for d in directors))
        add("constitution", "DMS", "Constitution", c["incorporated_on"],
            f"[p1]\nConstitution of {c['legal_name']}. Ordinary shares only.")

        lines = [f"Total issued shares: {total:,}",
                 f"Shareholder: {d1['name']} | {d1['shares_held']:,} shares | {d1['ownership_pct']}%"]
        if scenario == "obscured_register":
            s = f"{sh2['shares_held']:,}"
            lines.append(f"Shareholder: {sh2['name']} | {s[:2]}?{s[3:]} shares | [stamp obscures figure]")
        else:
            lines.append(f"Shareholder: {sh2['name']} | {sh2['shares_held']:,} shares | {sh2['ownership_pct']}%")
        add("shareholder_register", "DMS", "Shareholder register", as_of - timedelta(days=r.randint(60, 300)),
            "[p1]\n" + "\n".join(lines))

        if scenario != "missing_id":
            expiry = d2["passport_expiry"]
            add("director_ids", "DMS", "Director IDs (2)", prior - timedelta(days=r.randint(0, 20)),
                f"[p1]\n{d1['name']} NRIC on file. {d2['name']} passport on file, expiry {expiry}.", expiry=expiry)

        # Two years of audited FS, as a DMS would hold; Agent 1 picks the latest.
        fye = latest_fy_end(as_of)
        if scenario == "stale_fs":
            fye = fye.replace(year=fye.year - 1)
        for fy, rev in ((fye.replace(year=fye.year - 1), int(turnover / (1 + r.uniform(0.02, 0.2)))), (fye, turnover)):
            profit = int(rev * r.uniform(0.03, 0.12))
            add("audited_fs", "DMS", f"Audited FS FY{fy.year}", fy + timedelta(days=r.randint(100, 150)),
                f"[p{r.randint(4, 9)}]\nRevenue FY{fy.year}: SGD {rev:,}\nNet profit FY{fy.year}: SGD {profit:,}",
                fy_end=fy)

        screened = [c["legal_name"]] + [p["name"] for p in parties if p.get("is_director") or p.get("is_shareholder")]
        text = f"[p1]\nScreened: {', '.join(screened)}\n"
        if scenario == "screening_hit":
            hit = d2
            other = r.choice([n for n in ("MY", "IN", "PK", "AE", "GB") if n != hit["nationality"]])
            first, last = hit["name"].split(" ", 1)
            text += (f"Potential match: {hit['name']} | List: {r.choice(WATCHLISTS)} | Record: WC-{r.randint(1000000, 9999999)} "
                     f"| Listed name: {first} {chr(65 + r.randint(0, 25))}. {last} | "
                     f"Listed DOB: {(hit['dob'] - timedelta(days=r.randint(3000, 6000))).isoformat()} | "
                     f"Listed nationality: {other}\n")
        else:
            text += "No potential matches.\n"
        text += "Adverse media: none found"
        add("screening_report", "WorldCheck", "Screening report", as_of - timedelta(days=r.randint(0, 2)), text)

        inflow = int(turnover / 12 * r.uniform(0.7, 1.05) // 10_000 * 10_000)
        lane = f"{new_lane} added {as_of.year}" if new_lane else "No new trade lanes since last review"
        trade_prior = [t for t in trade if t != new_lane]
        add("prior_assessment", "DMS", f"Prior assessment {prior.year}", prior - timedelta(days=r.randint(1, 5)),
            f"[p3]\nTrade countries: {', '.join(trade_prior + ([new_lane] if new_lane else []))}\n"
            f"New trade lane: {lane}\nExpected monthly inflow: SGD {inflow:,}\n"
            f"UBO1 source of wealth: {d1['source_of_wealth']}\n"
            f"Prior directors: {'; '.join(d['name'] for d in directors)}")
        return docs

    # ------------------------------------------------------------------ many clients
    def clients(self, n: int, first_case_no: int = 1000) -> list[dict]:
        year = self.as_of.year
        return [self.client(SCENARIOS[i % len(SCENARIOS)], f"RA-{year}-{first_case_no + i:04d}") for i in range(n)]


def halcyon(proto: dict) -> dict:
    """The prototype's fixed Halcyon Marine case, as a client dict."""
    c = proto["case"]
    client = {"cif": c["client_id"], "legal_name": c["legal_name"], "short_name": c["short_name"], "uen": c["uen"],
              "segment": c["segment"], "incorporation_country": "SG",
              "principal_activity": "Freight forwarding and ship chartering", "ssic_code": "52291",
              "incorporated_on": date(2015, 7, 1), "prior_rating": c["prior_rating"],
              "prior_assessment_date": date.fromisoformat(c["prior_assessment_date"]),
              "next_review_date": date.fromisoformat(c["due_date"])}
    parties = [
        {"key": "d1", "party_type": "PERSON", "name": "Lim Wei Jie", "nationality": "SG", "is_director": True,
         "is_shareholder": True, "is_ubo": True, "ownership_pct": 55, "shares_held": 550_000,
         "appointed_on": date(2015, 7, 1), "id_document": "NRIC",
         "source_of_wealth": "Founder equity built from operating profits since 2015"},
        {"key": "d2", "party_type": "PERSON", "name": "Arun Menon", "nationality": "IN", "dob": date(1971, 4, 2),
         "is_director": True, "appointed_on": date(2019, 3, 2), "id_document": "Passport"},
        {"key": "s2", "party_type": "ENTITY", "name": "Seacrest Holdings Ltd (BVI)", "nationality": "BVI",
         "is_shareholder": True, "ownership_pct": 45, "shares_held": 450_000},
    ]
    case = {"case_ref": c["case_id"], "due_date": date.fromisoformat(c["due_date"]), "scenario": "halcyon_sample",
            "opened_at": datetime.fromisoformat(c["start_time"])}
    return {"client": client, "parties": parties, "documents": proto["document_store"], "case": case}
