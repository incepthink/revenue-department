"""Rerank of recalled precedents: a weighted mean of features in [0, 1], with the per-feature breakdown and
human-readable reasons (English + Marathi) for the officer screen. Pure; weights come from IntelConfig."""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from rci.intel.config import FEATURES
from rci.intel.profile import CaseProfile
from rci.intel.rulecompat import regime_similarity
from rci.pipeline.normalise import devanagari_digits

NEUTRAL = 0.5
TENURE_TEXT = {"occupancy_class_2": ("Occupancy Class-II", "भोगवटादार वर्ग-२"), "govt_lease": ("Government lease", "शासकीय भाडेपट्टा"),
               "occupancy_class_1": ("Occupancy Class-I", "भोगवटादार वर्ग-१")}


@dataclass
class Candidate:
    profile: CaseProfile
    card_id: str
    regime: tuple[str, ...] = ()  # rule sets in force on the precedent's rule date
    proc_regime: tuple[str, ...] = ()  # ... only those carrying requirements / obligations (procedure)
    sims: dict[str, float | None] = field(default_factory=dict)  # facet -> cosine (None = not comparable)
    features: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    breakdown: dict[str, dict] = field(default_factory=dict)
    reasons: list[dict] = field(default_factory=list)


def jaccard(a: set, b: set, empty: float = NEUTRAL) -> float:
    if not a and not b:
        return empty
    return len(a & b) / len(a | b)


def location(q: CaseProfile, p: CaseProfile) -> float:
    if q.village and p.village and q.village == p.village:
        return 1.0
    if q.taluka and p.taluka and q.taluka == p.taluka:
        return 0.6
    if q.district and p.district and q.district == p.district:
        return 0.3
    if not (q.district and p.district):
        return NEUTRAL
    return 0.0


def area(q: CaseProfile, p: CaseProfile) -> float:
    if not (q.area_sqm and p.area_sqm) or q.area_sqm <= 0 or p.area_sqm <= 0:
        return NEUTRAL
    return max(0.0, 1.0 - abs(math.log(float(q.area_sqm) / float(p.area_sqm))) / math.log(4))


def recency(q_date: dt.date | None, p_date: dt.date | None) -> float:
    if not (q_date and p_date):
        return NEUTRAL
    return math.exp(-abs((q_date - p_date).days) / 365.25 / 5)


def features(q: CaseProfile, c: Candidate, q_regime: tuple[str, ...], q_date: dt.date | None) -> dict[str, float]:
    p = c.profile
    sims = {f: (max(0.0, min(1.0, s)) if s is not None else NEUTRAL) for f, s in c.sims.items()}
    return {
        "sim_facts": sims.get("facts", NEUTRAL), "sim_documents": sims.get("documents", NEUTRAL), "sim_issues": sims.get("issues", NEUTRAL),
        "tenure": NEUTRAL if not (q.tenure and p.tenure) else float(q.tenure == p.tenure),
        "location": location(q, p), "area": area(q, p),
        "regime": regime_similarity(q_regime, c.regime),
        "recency": recency(q_date, p.as_of),
        "validations": jaccard(set(q.failed_validations), set(p.failed_validations)),
        "gaps": jaccard(q.open_codes, p.open_codes),
    }


def score(feats: dict[str, float], weights: dict[str, float]) -> tuple[float, dict[str, dict]]:
    total_w = sum(w for f, w in weights.items() if f in FEATURES and w > 0)
    breakdown = {}
    s = 0.0
    for f in FEATURES:
        w = max(0.0, weights.get(f, 0.0))
        contrib = w * feats[f] / total_w if total_w else 0.0
        s += contrib
        breakdown[f] = {"value": round(feats[f], 4), "weight": w, "contribution": round(contrib, 4)}
    return round(s, 6), breakdown


def _codes(codes: list[str], n: int = 3) -> str:
    return ", ".join(codes[:n]) + (f" +{len(codes) - n}" if len(codes) > n else "")


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def reasons(q: CaseProfile, c: Candidate, feats: dict[str, float], weights: dict[str, float]) -> list[dict]:
    """Sentences for the strongest agreements and the notable differences, most weighty first."""
    p = c.profile
    out: list[tuple[float, dict]] = []

    def add(feature: str, en: str, mr: str, kind: str = "match"):
        out.append((weights.get(feature, 0.0) * (feats[feature] if kind == "match" else 1 - feats[feature]),
                    {"feature": feature, "kind": kind, "en": en, "mr": mr}))

    if q.tenure and p.tenure:
        t_en, t_mr = TENURE_TEXT.get(p.tenure, (p.tenure, p.tenure))
        if q.tenure == p.tenure:
            add("tenure", f"Same holding basis: {t_en}", f"समान धारणाधिकार: {t_mr}")
        else:
            add("tenure", f"Different holding basis ({t_en})", f"वेगळा धारणाधिकार ({t_mr})", "difference")
    if feats["location"] == 1.0:
        add("location", f"Same village: {p.village}", f"समान गाव: {p.village}")
    elif feats["location"] >= 0.6:
        add("location", f"Same taluka: {p.taluka}", f"समान तालुका: {p.taluka}")
    elif feats["location"] >= 0.3:
        add("location", f"Same district: {p.district}", f"समान जिल्हा: {p.district}")
    if q.area_sqm and p.area_sqm:
        diff = abs(float(q.area_sqm) - float(p.area_sqm)) / float(q.area_sqm)
        if feats["area"] >= 0.8:
            add("area", f"Similar area: {p.area_sqm} vs {q.area_sqm} sq m ({_pct(diff)} apart)",
                f"जवळपास समान क्षेत्र: {devanagari_digits(str(p.area_sqm))} / {devanagari_digits(str(q.area_sqm))} चौ.मी.")
        elif feats["area"] < 0.3:
            add("area", f"Very different area: {p.area_sqm} vs {q.area_sqm} sq m",
                f"क्षेत्रात मोठा फरक: {devanagari_digits(str(p.area_sqm))} / {devanagari_digits(str(q.area_sqm))} चौ.मी.", "difference")
    shared_v = sorted(set(q.failed_validations) & set(p.failed_validations))
    if shared_v:
        add("validations", f"Same failed checks: {_codes(shared_v)}", f"समान तपासणी त्रुटी: {_codes(shared_v)}")
    shared_g = sorted(q.open_codes & p.open_codes)
    if shared_g:
        add("gaps", f"Same open requirements: {_codes(shared_g)}", f"समान प्रलंबित बाबी: {_codes(shared_g)}")
    if feats["regime"] == 1.0:
        add("regime", "Decided under the same rules", "समान नियमांखाली निर्णय")
    else:
        add("regime", f"Decided under different rules ({' < '.join(c.regime) or '—'})",
            f"वेगळ्या नियमांखाली निर्णय ({' < '.join(c.regime) or '—'})", "difference")
    for facet, label_en, label_mr in (("sim_issues", "issues", "मुद्दे"), ("sim_facts", "facts", "तथ्ये")):
        if c.sims.get(facet.removeprefix("sim_")) is not None and feats[facet] >= 0.6:
            add(facet, f"Similar {label_en} text ({feats[facet]:.2f})", f"{label_mr} मजकुरात साम्य ({feats[facet]:.2f})")
    if p.as_of:
        years = abs((q.rule_date() or p.as_of) - p.as_of).days / 365.25 if q.rule_date() else 0
        if years >= 3:
            add("recency", f"Decided {years:.0f} years apart", f"{devanagari_digits(f'{years:.0f}')} वर्षांचे अंतर", "difference")
    out.sort(key=lambda x: -x[0])
    return [r for w, r in out if w > 0][:6]


def rerank(q: CaseProfile, cands: list[Candidate], weights: dict[str, float], q_regime: tuple[str, ...],
           q_date: dt.date | None) -> list[Candidate]:
    for c in cands:
        c.features = features(q, c, q_regime, q_date)
        c.score, c.breakdown = score(c.features, weights)
        c.reasons = reasons(q, c, c.features, weights)
    return sorted(cands, key=lambda c: (-c.score, c.profile.case_ref))
