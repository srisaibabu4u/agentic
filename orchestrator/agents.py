"""The five specialised agents.

Every agent has the same shape: run(task, case) -> result dict (the JSON
contract in prompts/<agent>.md). Two implementations sit behind each one:

* offline (default): deterministic Python that follows the same rules as the
  prompt, so the whole workflow runs with no API key and is repeatable;
* live: an autonomous Claude agent. It gets its system prompt from
  prompts/*.md, a short task brief and read-only tools (toolkit.py), plans its
  own steps, gathers and checks evidence, and submits a result that is
  validated against the evidence before the master sees it (llm.run_agent).

Agents never write to the case and never call each other. The engine applies
their results to the case and hands the result event to the master.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from . import toolkit as tk
from .llm import LLM
from .models import A1, A2, A3, A4, A5, Case, Task


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _months_between(a: date, b: date) -> float:
    return (b - a).days / 30.44


def _money(n: float) -> str:
    return f"SGD {n / 1e6:.1f}m"


class BaseAgent:
    agent_id = ""
    prompt_file = ""
    required_keys: tuple = ()
    tools_fn = validate_fn = None   # toolkit functions for live mode

    def __init__(self, cfg: dict, llm: LLM | None = None):
        self.cfg, self.llm = cfg, llm

    def run(self, task: Task, case: Case, now: datetime) -> dict:
        if self.llm:
            res = self.llm.run_agent(self.prompt_file, self.brief(task, case, now), self.tools(task, case, now),
                                     lambda res: self.validate(task, case, now, res), bank_name="Example Bank")
        else:
            res = self.offline(task, case, now)
        if res.get("status") == "FAILED":  # the master retries the same work, so it needs the scope back
            res.setdefault("scope", task.scope)
            res.setdefault("inputs", task.inputs)
        return res

    def tools(self, task: Task, case: Case, now: datetime) -> list[tk.Tool]:
        return type(self).tools_fn(self, task, case, now)

    def validate(self, task: Task, case: Case, now: datetime, res: dict) -> list[str]:
        """Checks a submitted result against the evidence; [] means accepted."""
        missing = [k for k in self.required_keys if k not in res]
        if missing:
            return [f"result is missing keys {missing}"]
        return type(self).validate_fn(self, task, case, now, res)

    def brief(self, task: Task, case: Case, now: datetime) -> dict:
        """What the agent is told up front; everything else it looks up with its tools."""
        return {"task": task.__dict__, "now": now.isoformat(),
                "case": {k: case.meta.get(k) for k in ("legal_name", "uen", "segment", "due_date")} | {"case_id": case.case_id}}

    # subclasses implement
    def offline(self, task: Task, case: Case, now: datetime) -> dict: ...


# ============================================================ Agent 1 · Discovery
class DiscoveryAgent(BaseAgent):
    agent_id, prompt_file, required_keys = A1, "a1_discovery.md", ("status", "found", "missing")
    tools_fn, validate_fn = tk.discovery_tools, tk.validate_discovery

    def search_all_sources(self, case: Case) -> list[dict]:
        """Stands in for search_dms / search_crm / fetch_registry / run_screening tools."""
        return [d for d in self.cfg["document_store"]] + [d for d in case.docs.values() if d["doc_id"] not in
                                                          {x["doc_id"] for x in self.cfg["document_store"]}]

    def is_fresh(self, doc: dict, now: datetime, due: date) -> tuple[bool, str]:
        item, today = doc["item"], now.date()
        if item == "registry_extract":
            ok = (today - _d(doc["date"])).days <= 30
            return ok, "" if ok else f"Registry extract dated {doc['date']} is older than 30 days."
        if item == "audited_fs":
            m = _months_between(_d(doc["fy_end"]), due)
            ok = m <= 18
            return ok, "" if ok else (f"Latest FS on file is FY{doc['fy_end'][:4]} (FYE {doc['fy_end']}), "
                                      f"{m:.0f} months before the review date; limit is 18.")
        if item == "director_ids":
            ok = _d(doc.get("expiry", "2999-12-31")) > due
            return ok, "" if ok else "An ID expires before the review date."
        if item == "screening_report":
            ok = (today - _d(doc["date"])).days <= 7
            return ok, "" if ok else "Screening older than 7 days."
        return True, ""

    def offline(self, task, case, now):
        due = _d(case.meta["due_date"])
        docs = self.search_all_sources(case)
        found, missing = [], []
        for item in self.cfg["checklist"]["items"]:
            cands = sorted([d for d in docs if d["item"] == item["item"]], key=lambda d: d["date"], reverse=True)
            if not cands:
                missing.append({"checklist_item": item["item"], "label": item["label"],
                                "identity_critical": item["identity_critical"], "reason": "Not found in any source."})
                continue
            best = cands[0]
            fresh, why = self.is_fresh(best, now, due)
            found.append({"doc_id": best["doc_id"], "checklist_item": item["item"], "type": best["type"],
                          "source": best["source"], "doc_date": best["date"], "fresh": fresh,
                          "alternatives": [c["doc_id"] for c in cands[1:]]})
            if not fresh:
                missing.append({"checklist_item": item["item"], "label": item["label"],
                                "identity_critical": item["identity_critical"], "reason": why})
        return {"status": "PARTIAL" if missing else "DONE", "found": found, "missing": missing,
                "source_errors": [], "confidence": 0.95}



# ============================================================ Agent 2 · Ingestion
# (doc item, field id, regex, how to build the value)
EXTRACTORS = [
    ("registry_extract", "entity.name", r"Name:\s*(.+)", "str"),
    ("registry_extract", "entity.uen", r"UEN:\s*(\S+)", "str"),
    ("registry_extract", "entity.activity", r"Principal activity:\s*(.+)", "str"),
    ("registry_extract", "directors", r"Director:\s*([^|]+?)\s*\|\s*Nationality:\s*(\w+)", "list_dir"),
    ("shareholder_register", "ubo.1", r"Shareholder:\s*([^|]+?)\s*\|\s*([\d,?]+) shares\s*\|\s*(\d+)%", "sh:0"),
    ("shareholder_register", "ubo.2", r"Shareholder:\s*([^|]+?)\s*\|\s*([\d,?]+) shares\s*\|\s*(.+)", "sh:1"),
    ("audited_fs", "turnover.fy{yr}", r"Revenue FY(\d{4}):\s*SGD ([\d,]+)", "money"),
    ("screening_report", "screening.hits", r"Potential match:\s*(.+)", "hits"),
    ("screening_report", "screening.hits", r"No potential matches", "no_hits"),  # a clear report is evidence too
    ("screening_report", "adverse_media", r"Adverse media:\s*(.+)", "str"),
    ("prior_assessment", "trade.countries", r"Trade countries:\s*(.+)", "csv"),
    ("prior_assessment", "trade.new_lane", r"New trade lane:\s*(.+)", "str"),
    ("prior_assessment", "activity.monthly_inflow", r"Expected monthly inflow:\s*SGD ([\d,]+)", "num"),
    ("prior_assessment", "ubo.1.sow", r"UBO1 source of wealth:\s*(.+)", "str"),
    ("prior_assessment", "ubo.2.sow", r"UBO2 source of wealth:\s*(.+)", "str"),   # when UBO2 is a person
    ("prior_assessment", "directors.prior", r"Prior directors:\s*(.+)", "semi"),
    ("ubo_corporate_register", "ubo.2.owner", r"Member:\s*([^|]+?)\s*\|\s*Nationality:\s*(\w+)\s*\|\s*(\d+)%", "member"),
    ("sow_declaration", "ubo.2.sow", r"Source of wealth:\s*(.+)", "str"),
]


class IngestionAgent(BaseAgent):
    agent_id, prompt_file, required_keys = A2, "a2_ingestion.md", ("status", "fields", "flags")
    tools_fn, validate_fn = tk.ingestion_tools, tk.validate_ingestion

    @staticmethod
    def _page(text: str, pos: int) -> int:
        pages = [(m.start(), int(m.group(1))) for m in re.finditer(r"\[p(\d+)\]", text)]
        return max([p for s, p in pages if s <= pos] or [1])

    def offline(self, task, case, now):
        fields, flags = {}, []
        for doc_id in task.scope["docs"]:
            doc = case.docs[doc_id]
            text = doc["text"]
            for item, fid, rx, kind in EXTRACTORS:
                if item != doc["item"]:
                    continue
                ms = list(re.finditer(rx, text))
                if not ms:
                    continue
                conf, note = 0.97, ""
                m = ms[0]
                if kind == "list_dir":
                    val, raw = [f"{x.group(1)} ({x.group(2)})" for x in ms], "; ".join(x.group(0) for x in ms)
                elif kind.startswith("sh:"):
                    i = int(kind[3:])
                    if i >= len(ms):
                        continue
                    m = ms[i]
                    raw = m.group(0)
                    pct_txt = m.group(3)
                    if "?" in m.group(2) or not pct_txt.strip().rstrip("%").isdigit():
                        total = 100 - sum(v["value"]["pct"] for k, v in fields.items() if k.startswith("ubo.") and isinstance(v["value"], dict))
                        val = {"name": m.group(1).strip(), "pct": total}
                        conf, note = 0.62, f"Share count partly obscured ('{m.group(2)}'); {total}% inferred from total issued shares."
                    else:
                        val = {"name": m.group(1).strip(), "pct": int(pct_txt.strip().rstrip('%'))}
                        conf, note = 0.96, f"pct read directly; share count {m.group(2)}"
                elif kind == "money":
                    fid = fid.format(yr=m.group(1))
                    val, raw = int(m.group(2).replace(",", "")), m.group(0)
                elif kind == "num":
                    val, raw = int(m.group(1).replace(",", "")), m.group(0)
                elif kind == "csv":
                    val, raw, conf = [c.strip() for c in m.group(1).split(",")], m.group(0), 0.88
                    note = "Taken from prior assessment; not re-evidenced this cycle."
                elif kind == "semi":
                    val, raw = [c.strip() for c in m.group(1).split(";")], m.group(0)
                elif kind == "hits":
                    val = []
                    for x in ms:
                        parts = dict(p.split(":", 1) if ":" in p else ("name", p) for p in x.group(1).split("|"))
                        parts = {k.strip().lower(): v.strip() for k, v in parts.items()}
                        val.append(parts)
                    raw, conf = "; ".join(x.group(0) for x in ms), 0.90
                    flags.append({"code": "SCREENING_HIT", "ref": "screening.hits",
                                  "detail": f"{len(val)} potential match(es) need a maker disposition"})
                elif kind == "no_hits":
                    if fid in fields:
                        continue
                    val, raw = [], m.group(0)
                elif kind == "member":
                    val = {"name": m.group(1).strip(), "nationality": m.group(2), "pct_of_holding": int(m.group(3))}
                    raw = m.group(0)
                else:
                    val, raw = m.group(1).strip(), m.group(0)
                fields[fid] = {"value": val, "raw": raw, "confidence": conf,
                               "source": f"{doc_id}#p{self._page(text, m.start())}", "conflict": False, "note": note}
                if conf < 0.80:
                    flags.append({"code": "LOW_CONF_FIELD", "ref": fid, "detail": note})
        return {"status": "DONE" if fields else "FAILED", "fields": fields, "flags": flags}



# ============================================================ Agent 3 · Drafting
RISK_WORD = {1: "low-risk", 2: "standard-risk", 3: "elevated-risk", 4: "high-risk", 5: "very high-risk"}
CORPORATE = re.compile(r"\b(Ltd|Limited|Inc|LLC|Pte|Holdings|Corp)\b")
JURISDICTION = {"BVI": "BVI", "Cayman": "KY", "HK": "HK"}   # name suffix "(BVI)" -> country code


def _lc(s: str) -> str:
    return s[0].lower() + s[1:]


def jurisdiction(name: str) -> str | None:
    m = re.search(r"\((\w+)\)\s*$", name)
    return JURISDICTION.get(m.group(1), m.group(1)) if m else None


class DraftingAgent(BaseAgent):
    agent_id, prompt_file, required_keys = A3, "a3_drafting.md", ("status", "mode", "answers")
    tools_fn, validate_fn = tk.drafting_tools, tk.validate_drafting

    def _f(self, case, fid):
        f = case.fields.get(fid)
        return f["value"] if f else None

    @staticmethod
    def _turnover(case) -> list[tuple[int, int]]:
        """(year, revenue) for every turnover.fyYYYY field, oldest first."""
        return sorted((int(k[-4:]), v["value"]) for k, v in case.fields.items() if re.fullmatch(r"turnover\.fy\d{4}", k))

    @staticmethod
    def _fs_fresh(case) -> bool:
        return any(d["item"] == "audited_fs" and d.get("fresh", True) for d in case.docs.values())

    def _flags(self, case, fids):
        fl = []
        for fid in fids:
            if fid in case.open_items:
                fl.append("NEEDS_HUMAN")
        return sorted(set(fl))

    def answer(self, q: str, case: Case, notes: list) -> dict:
        f, cr = (lambda k: self._f(case, k)), self.cfg["questionnaire"]["country_risk"]
        note_txt = " ".join(n.get("issue", "") + n.get("comment", "") for n in notes if n.get("q_id") == q)

        if q == "Q1":
            activity = f("entity.activity") or ""
            ssic = re.search(r"SSIC (\d+)", activity)
            entry = self.cfg["questionnaire"].get("industry_risk", {}).get(ssic.group(1) if ssic else "")
            if entry:
                label, lvl = entry
                score, reason = lvl, f"{label}: {RISK_WORD[lvl]} industry"
            else:
                score, reason = 3, "Industry not on the reference list; treated as elevated-risk"
            return dict(answer=f"{f('entity.name')} ({f('entity.uen')}) carries on {activity}.",
                        score=score, score_reason=reason, cites=["entity.name", "entity.activity"])
        if q == "Q2":
            u1, u2 = f("ubo.1"), f("ubo.2")
            owners = [u for u in (u1, u2) if u]
            ok = sum(u["pct"] for u in owners) == 100
            corp = [u for u in owners if CORPORATE.search(u["name"])]
            off = [u for u in corp if cr.get(jurisdiction(u["name"]) or "SG", 3) >= 4]
            confirmed = "ubo.2" in case.maker_inputs["edits"]
            held = "; ".join(f"{u['name']} holds {u['pct']}%" for u in owners)
            if u2:
                held += ' (confirmed by maker against signed register)' if confirmed else ' (percentage not confirmed)'
            score, reason = ((3, "Offshore corporate shareholder") if off else
                             (2, "Onshore corporate shareholder") if corp else (2, "Natural-person owners"))
            return dict(answer=f"{held}. Total {'100%' if ok else 'does not reconcile to 100%'}.",
                        score=score, score_reason=reason, cites=["ubo.1", "ubo.2"] if u2 else ["ubo.1"])
        if q == "Q3":
            cs = f("trade.countries") or []
            top = max(cs, key=lambda c: cr.get(c, 3))
            return dict(answer=f"Trade corridors: {', '.join(cs)}. Highest country risk is {top} "
                               f"(level {cr.get(top, 3)} on country list).",
                        score=max(cr.get(c, 3) for c in cs), score_reason="Highest corridor risk", cites=["trade.countries"])
        if q == "Q4":
            years, fresh = self._turnover(case), self._fs_fresh(case)
            if len(years) >= 2:
                (y0, t0), (y1, t1) = years[-2:]
                g = (t1 - t0) / t0 * 100
                score, reason = ((3, "Rapid turnover growth") if g > 50 else (3, "Turnover decline") if g < -20
                                 else (2, "Audited, stable growth"))
                return dict(answer=f"Turnover {_money(t1)} in FY{y1}, {'up' if g >= 0 else 'down'} {abs(g):.0f}% "
                                   f"from {_money(t0)} in FY{y0}.",
                            score=score, score_reason=reason, cites=[f"turnover.fy{y0}", f"turnover.fy{y1}"],
                            extra_flags=[] if fresh else ["STALE_EVIDENCE"])
            if years:
                y, t = years[-1]
                if fresh:
                    return dict(answer=f"Turnover {_money(t)} in FY{y} (audited).", score=2,
                                score_reason="Audited, current", cites=[f"turnover.fy{y}"])
                return dict(answer=f"Turnover {_money(t)} in FY{y}. FY{y + 1} audited statements not yet received.",
                            score=2, score_reason="Audited but stale", cites=[f"turnover.fy{y}"], extra_flags=["STALE_EVIDENCE"])
            return dict(answer="No audited financial statements on file.", score=None,
                        score_reason="No financial evidence", cites=[])
        if q == "Q5":
            inflow, years = f("activity.monthly_inflow"), self._turnover(case)
            if not inflow or not years:
                return dict(answer="Expected account activity cannot be assessed: inflow or turnover not on file.",
                            score=None, score_reason="Missing evidence", cites=[])
            y, t = years[-1]
            high = inflow * 12 > 1.5 * t
            return dict(answer=f"Expected inflows of {_money(inflow)} a month ({_money(inflow * 12)} a year), "
                               f"{'well above' if high else 'consistent with'} turnover of {_money(t)}.",
                        score=4 if high else 2,
                        score_reason="Expected activity well above turnover" if high else "Activity consistent with turnover",
                        cites=["activity.monthly_inflow", f"turnover.fy{y}"])
        if q == "Q6":
            hits, disp = f("screening.hits") or [], case.maker_inputs["dispositions"].get("screening.hits")
            if not hits:
                return dict(answer="No screening matches.", score=1, score_reason="Clear", cites=["screening.hits"])
            h = hits[0]
            if not disp:
                return dict(answer=f"1 potential match on {h['name']}; disposition pending.", score=None,
                            score_reason="Open hit", cites=["screening.hits"])
            reason = re.sub(r"^False positive\.\s*", "", disp)
            if "record" in note_txt.lower():  # redraft resolving reviewer note: add list and record id
                ans = (f"{h['list']} record {h['record']} ({h['listed name']}) dismissed by the maker as a false positive: "
                       f"{reason}")
            else:  # first draft restates the maker's disposition as given
                ans = f"1 potential match on {h['name']} dismissed by the maker: {disp}"
            return dict(answer=ans, score=1, score_reason="Match dismissed with rationale", cites=["screening.hits", "maker.disposition.screening.hits"])
        if q == "Q7":
            u1, u2 = f("ubo.1"), f("ubo.2")
            owner, sow2 = f("ubo.2.owner"), f("ubo.2.sow")
            if owner and sow2:  # corporate shareholder, looked through to its owner
                return dict(answer=f"{u1['name']} ({u1['pct']}%): {_lc(f('ubo.1.sow'))}. {owner['name']} ({owner['nationality']}) "
                                   f"owns {owner['pct_of_holding']}% of {u2['name']} ({u2['pct']}%): {_lc(sow2)}.",
                            score=2, score_reason="SoW evidenced for every UBO", cites=["ubo.1.sow", "ubo.2.owner", "ubo.2.sow"])
            if u2 and sow2 and not CORPORATE.search(u2["name"]):  # second owner is a person
                return dict(answer=f"{u1['name']} ({u1['pct']}%): {_lc(f('ubo.1.sow'))}. "
                                   f"{u2['name']} ({u2['pct']}%): {_lc(sow2)}.",
                            score=2, score_reason="SoW evidenced for every UBO", cites=["ubo.1.sow", "ubo.2.sow"])
            return dict(answer=f"{u1['name']}: {f('ubo.1.sow').lower()}.", score=2,
                        score_reason="Founder SoW on file", cites=["ubo.1.sow"])
        if q == "Q8":
            now_d = [d.split(" (")[0] for d in f("directors") or []]
            changed = sorted(now_d) != sorted(f("directors.prior") or [])
            lane = f("trade.new_lane") or "No new trade lanes since last review"
            new_lane = not lane.lower().startswith("no new")
            reasons = [r for r, on in (("Director changes", changed), ("New corridor added", new_lane)) if on]
            return dict(answer=f"{'Director changes' if changed else 'No director changes'} since last review. {lane}.",
                        score=1 + changed + new_lane, score_reason=" and ".join(reasons) or "No material changes",
                        cites=["directors", "directors.prior", "trade.new_lane"])
        raise ValueError(q)

    def suggest(self, q: str, case: Case, notes: list) -> dict:
        u2 = self._f(case, "ubo.2") or {"name": "[corporate shareholder]"}
        return dict(answer=f"{u2['name']} is owned by [__] ([nationality], [__]%). Their wealth derives from [__], "
                           f"evidenced by [document].",
                    evidence_needed=[f"{u2['name']} register of members", "Source-of-wealth declaration with supporting evidence"])

    def offline(self, task, case, now):
        mode, notes = task.scope["mode"], task.inputs.get("notes", [])
        out = []
        for q in task.scope["questions"]:
            if mode == "suggest":
                s = self.suggest(q, case, notes)
                out.append({"q_id": q, "answer": s["answer"], "score": None, "citations": [], "flags": ["SUGGESTION"],
                            "confidence": 0.5, "evidence_needed": s["evidence_needed"]})
                continue
            a = self.answer(q, case, notes)
            cited = [c for c in a["cites"] if not c.startswith("maker.")]
            conf = min([case.fields[c]["confidence"] for c in cited if c in case.fields] or [0.9])
            flags = self._flags(case, cited) + a.get("extra_flags", [])
            if a["score"] is None:
                flags.append("NEEDS_HUMAN")
            res = {"q_id": q, "answer": a["answer"], "score": a["score"], "score_reason": a["score_reason"],
                   "citations": a["cites"], "flags": sorted(set(flags)), "confidence": round(conf, 2)}
            note = next((n for n in notes if n.get("q_id") == q), None)
            if note:
                res["resolves"] = note.get("issue") or note.get("comment")
            out.append(res)
        return {"status": "DONE", "mode": mode, "answers": out}



def compute_rating(answers: dict, bands: list) -> dict:
    scores = [a.get("score") for a in answers.values()]
    if any(s is None for s in scores):
        return {"total": sum(s or 0 for s in scores), "max": 5 * len(scores), "working": "has unscored answers", "band": "PROVISIONAL"}
    total = sum(scores)
    band = next(b["band"] for b in bands if total <= b["max"])
    return {"total": total, "max": 5 * len(scores), "working": "+".join(map(str, scores)), "band": band}


# ============================================================ Agent 4 · Review
class ReviewAgent(BaseAgent):
    agent_id, prompt_file, required_keys = A4, "a4_review.md", ("verdict", "findings")
    tools_fn, validate_fn = tk.review_tools, tk.validate_review

    def offline(self, task, case, now):
        findings, checks = [], {}
        policy = self.cfg["questionnaire"]["policy"]
        ans = case.answers
        n_ok = 0
        for q in task.scope["questions"]:
            a = ans[q]
            # C1 evidence: every citation must exist
            bad = [c for c in a["citations"] if not c.startswith("maker.") and c not in case.fields]
            if bad:
                findings.append({"q_id": q, "check": "C1", "severity": "HIGH", "excerpt": a["answer"][:120],
                                 "issue": f"Cites missing fields {bad}", "policy_ref": None, "evidence_available": False})
            else:
                n_ok += 1
            # C3 open items
            if set(a["citations"]) & set(case.open_items) or "NEEDS_HUMAN" in a["flags"]:
                findings.append({"q_id": q, "check": "C3", "severity": "HIGH", "excerpt": a["answer"][:120],
                                 "issue": "Uses a value that still needs human confirmation", "policy_ref": None, "evidence_available": False})
            # C7 freshness
            if "STALE_EVIDENCE" in a["flags"]:
                findings.append({"q_id": q, "check": "C7", "severity": "MEDIUM", "excerpt": a["answer"][:120],
                                 "issue": "Relies on stale evidence", "policy_ref": None, "evidence_available": False})
        checks["C1"] = f"{n_ok}/{len(task.scope['questions'])}"
        # C2 policy 7.2.4: screening disposition must state list name + record id
        hits = (case.fields.get("screening.hits") or {}).get("value") or []
        if hits and "Q6" in ans:
            h, txt = hits[0], ans["Q6"]["answer"]
            if not (h["record"] in txt and h["list"] in txt):
                findings.append({"q_id": "Q6", "check": "C2", "severity": "MEDIUM", "excerpt": txt[:120],
                                 "issue": "Disposition must state the screening list name and record ID.",
                                 "policy_ref": "7.2.4", "evidence_available": True})
        checks["C2"] = "pass" if not any(f["check"] == "C2" for f in findings) else "fail"
        # C4 UBO sum
        u = [case.fields[k]["value"]["pct"] for k in ("ubo.1", "ubo.2") if k in case.fields]
        if u and sum(u) != 100:
            findings.append({"q_id": "Q2", "check": "C4", "severity": "HIGH", "excerpt": ans["Q2"]["answer"][:120],
                             "issue": f"UBO percentages sum to {sum(u)}%", "policy_ref": "7.1.2", "evidence_available": False})
        checks["C4"] = f"UBO sum {sum(u)}%"
        # C6 rating recompute
        rec = compute_rating(ans, self.cfg["questionnaire"]["rating_bands"])
        checks["C6"] = "pass" if case.rating and rec["total"] == case.rating["total"] else "mismatch"
        bad = [f for f in findings if f["severity"] in ("HIGH", "MEDIUM")]
        return {"verdict": "FAIL" if bad else "PASS", "findings": findings, "checks_summary": checks,
                "recomputed_rating": {**rec, "matches_draft": checks["C6"] == "pass"}}



# ============================================================ Agent 5 · Notification
class NotificationAgent(BaseAgent):
    agent_id, prompt_file, required_keys = A5, "a5_notification.md", ("sent",)
    tools_fn, validate_fn = tk.notification_tools, tk.validate_notification

    LEAD = {
        "DOC_REQUEST": "please upload {n} document(s) so drafting can complete",
        "MAKER_REVIEW": "please resolve {n} item(s) on the draft and submit",
        "CHECKER_REVIEW": "the case is ready for your approval",
        "CHECKER_RETURN": "the checker returned {n} question(s); a suggested redraft is ready",
        "CASE_CLOSED": "the case is approved and closed",
        "ESCALATION": "the workflow needs you to unblock it",
    }

    def offline(self, task, case, now):
        i, M = task.inputs, case.meta
        people = {p["id"]: p for p in (M["maker"], M["checker"], M["team_lead"])}
        sent = []
        for rid in i["to"]:
            p = people[rid]
            lead = self.LEAD[i["template"]].format(n=len(i["items"]))
            lines = [f"{p['name'].split()[0]}, {lead}."]
            lines += [f"{k}. {it}" for k, it in enumerate(i["items"], 1)]
            lines.append(f"Case {case.case_id}, {M['legal_name']}, due {_d(M['due_date']).strftime('%d %b %Y')}.")
            lines.append(M["case_url"])
            if i.get("escalate_cc"):
                lines.append(f"Due in fewer than 5 business days.")
            action = {"DOC_REQUEST": "documents needed", "MAKER_REVIEW": f"{len(i['items'])} items to resolve",
                      "CHECKER_REVIEW": "ready for approval", "CHECKER_RETURN": "returned by checker",
                      "CASE_CLOSED": "approved and closed", "ESCALATION": "action needed"}[i["template"]]
            sent.append({"channel": "task_inbox+email", "to": f"{p['name']} · {p['role']}",
                         "cc": [people[i["escalate_cc"]]["name"]] if i.get("escalate_cc") else [],
                         "subject": f"{case.case_id} {M['short_name']}: {action}", "body": "\n".join(lines),
                         "message_id": f"m-{case.task_seq:03d}{len(sent)}"})
        return {"sent": sent, "action_items": i["items"]}



def build_agents(cfg: dict, llm: LLM | None, live_agents: set[str]) -> dict:
    classes = [DiscoveryAgent, IngestionAgent, DraftingAgent, ReviewAgent, NotificationAgent]
    return {c.agent_id: c(cfg, llm if c.agent_id in live_agents else None) for c in classes}
