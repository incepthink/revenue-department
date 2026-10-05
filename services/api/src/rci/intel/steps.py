"""Next steps: what usually happens next in cases like this one, and what the rules or the case facts demand.

Per step (event-catalog code, or an extra step such as the lease-rent dues check):
  frequency          weighted share of precedents in which the event happened
  typical_position   weighted median of its relative position in the precedent's event sequence (0 = first)
  median_days_from_filing / median_days_from_previous   weighted medians over the precedents' event dates
  triggers           rule obligations in force for the query (→ STATUTORY) and fact triggers (area mismatch →
                     CTSO measurement; leasehold → lease-rent dues check; ...), each cited
Steps already on the query's own timeline are listed as done; the rest are reported when frequent enough
(`step_threshold`) or triggered.
"""
from __future__ import annotations

import datetime as dt

from rci.intel.profile import CaseProfile, cite_fact
from rci.intel.rank import Candidate
from rci.intel.rulecompat import Support, label

MAX_CITES = 3


def weighted_median(pairs: list[tuple[float, float]]) -> float | None:
    """pairs = [(value, weight)]; the lower weighted median (deterministic, no interpolation)."""
    pairs = sorted((v, w) for v, w in pairs if w > 0)
    if not pairs:
        return None
    half = sum(w for _, w in pairs) / 2
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= half:
            return v
    return pairs[-1][0]


def _fact_trigger(q: CaseProfile, t: dict) -> tuple[bool, list[dict]]:
    ok, cites = True, []
    for key, want in (t.get("when") or {}).items():
        if key == "validator_failed":
            ok &= want in q.failed_validations
            cites += q.validator_evidence(want)
        elif key == "tenure":
            ok &= q.tenure == want
            c = cite_fact(q.card, (q.facts.get("land") or {}).get("tenure"))
            cites += [c] if c else []
        elif key == "encroachment":
            ok &= q.encroachment is want
            for i in q.facts.get("inspections") or []:
                c = cite_fact(q.card, i.get("encroachment"))
                cites += [c] if c else []
        else:
            ok = False
    return ok, cites


def next_steps(q: CaseProfile, precs: list[Candidate], event_catalog: list[dict], extra_steps: dict[str, dict],
               obligations_now: list[dict], obligations_then: dict[str, set[str]], obligation_steps: dict[str, list[str]],
               fact_triggers: list[dict], q_proc_regime: tuple[str, ...], step_threshold: float) -> dict:
    catalog = {e["code"]: e for e in event_catalog}
    catalog.update({code: {"code": code, **spec} for code, spec in extra_steps.items()})
    done = {e["code"]: e for e in q.events}
    filed = q.application_date
    w_total = sum(c.score for c in precs)
    step_of_obligation = obligation_steps

    steps = []
    for code, ev in catalog.items():
        having = []
        pos, from_filing, from_prev = [], [], []
        for c in precs:
            seq = c.profile.events
            idx = next((i for i, e in enumerate(seq) if e["code"] == code), None)
            if idx is None:
                continue
            having.append(c)
            e = seq[idx]
            pos.append((idx / (len(seq) - 1) if len(seq) > 1 else 0.0, c.score))
            if e["date"] and c.profile.application_date:
                from_filing.append(((e["date"] - c.profile.application_date).days, c.score))
            prev = next((p for p in reversed(seq[:idx]) if p["date"]), None)
            if e["date"] and prev:
                from_prev.append(((e["date"] - prev["date"]).days, c.score))
        freq = sum(c.score for c in having) / w_total if w_total else 0.0
        triggers = []
        for ob in obligations_now:
            if ob.get("applies") is not False and code in step_of_obligation.get(ob["code"], []):
                triggers.append({"kind": "rule", "code": ob["code"], "rule_set_code": ob["rule_set_code"], "status": ob["status"],
                                 "applies": ob.get("applies"), "needs": ob.get("needs", []), "source_ref": ob.get("source_ref"),
                                 "text_en": ob.get("description_en"), "text_mr": ob.get("description_mr")})
        for t in fact_triggers:
            if t["step"] != code:
                continue
            ok, cites = _fact_trigger(q, t)
            if ok:
                triggers.append({"kind": "fact", "code": t["code"], "text_en": t.get("reason_en"), "text_mr": t.get("reason_mr"),
                                 "evidence": cites})
        statutory = any(t["kind"] == "rule" and t.get("applies") is True for t in triggers)
        supports = [Support(c.score, c.proc_regime == q_proc_regime,
                            any(code in step_of_obligation.get(o, []) for o in obligations_then.get(c.profile.case_id, set())))
                    for c in having]
        median_filing = weighted_median(from_filing)
        row = {
            "code": code, "name_en": ev.get("name_en"), "name_mr": ev.get("name_mr"), "actor": ev.get("actor"),
            "kind": "event" if code in {e["code"] for e in event_catalog} else "check",
            "typical_order": ev.get("typical_order"),
            "frequency": round(freq, 3), "n_precedents": len(having),
            "typical_position": round(weighted_median(pos), 3) if pos else None,
            "median_days_from_filing": median_filing, "median_days_from_previous": weighted_median(from_prev),
            "expected_by": (filed + dt.timedelta(days=median_filing)).isoformat() if filed and median_filing is not None else None,
            "basis": label(statutory, supports),
            "triggers": triggers,
            "citations": [{**c.profile.events[next(i for i, e in enumerate(c.profile.events) if e["code"] == code)]["cite"],
                           "date": next(e["date"] for e in c.profile.events if e["code"] == code), "score": c.score}
                          for c in sorted(having, key=lambda c: -c.score)[:MAX_CITES]],
        }
        for cit in row["citations"]:
            cit["date"] = cit["date"].isoformat() if cit["date"] else None
        if code in done:
            d = done[code]
            row.update(status="done", date=d["date"].isoformat() if d["date"] else None, own_evidence=d["cite"])
        elif triggers or freq >= step_threshold:
            row["status"] = "next"
        else:
            continue
        steps.append(row)
    # Steps no precedent shows get a position between the catalog neighbours that have one, so the timeline
    # keeps the catalog order (e.g. premium paid before the order) instead of trailing after everything.
    known = [(s["typical_order"], s["typical_position"]) for s in steps
             if s["typical_position"] is not None and s["typical_order"] is not None]
    for s in steps:
        pos = s["typical_position"]
        if pos is None and s["typical_order"] is not None:
            lo = max((p for o, p in known if o <= s["typical_order"]), default=0.0)
            hi = min((p for o, p in known if o >= s["typical_order"]), default=1.0)
            pos = (lo + hi) / 2 if lo <= hi else lo
        s["position_for_order"] = round(pos if pos is not None else 1.0, 4)
    key = lambda s: (s["position_for_order"], s["typical_order"] or 999, s["code"])  # noqa: E731
    return {"done": sorted([s for s in steps if s["status"] == "done"], key=lambda s: (s.get("date") or "9999", key(s))),
            "next": sorted([s for s in steps if s["status"] == "next"], key=key)}
