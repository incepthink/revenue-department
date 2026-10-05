"""Gap analysis: which requirements the incoming case still lacks, with the evidence for each.

For every catalog requirement that is open in the query case (missing / invalid / queried):
  * support          weighted share (rerank score as weight) of *applicable* precedents that needed it: it was
                     present, invalid or queried there. Precedents where it was not_applicable are left out; one
                     decided with it still missing counts against it.
  * was_queried_in   the precedents that asked for it in a query letter, each cited to the letter's page.
  * statutory        the rule stack in force on the query's rule date requires it (cited to the rule row).
  * validator        a failed validator on the query's own documents makes it invalid / needed (cited to pages).
It is reported when it is statutory, validator-driven, or supported by ≥ `support_threshold` of at least
`min_support_cases` applicable precedents. Each gap carries its rule-compatibility label (regime = the procedural
rule sets, i.e. those with requirement / obligation rows: a premium-only notification does not change practice).
Gaps are what the applicant must still supply: office-process requirements (site inspection, CTSO measurement) are
the office's own next steps, and payment requirements become gaps only once the demand letter has issued.
"""
from __future__ import annotations

from dataclasses import dataclass

from rci.intel.profile import NEEDED, OPEN, CaseProfile, requirement_validators
from rci.intel.rank import Candidate
from rci.intel.rulecompat import LABELS, Support, label

MAX_CITES = 5


@dataclass(frozen=True)
class GapSettings:
    support_threshold: float
    min_support_cases: int


def _precedent_cite(c: Candidate, code: str) -> dict | None:
    p = c.profile
    status = p.req_status.get(code)
    if code in p.req_queried:
        return {**p.req_queried[code], "how": "queried", "score": c.score}
    if status in NEEDED and code in p.req_satisfied:
        return {**p.req_satisfied[code], "how": status, "score": c.score}
    return None


def analyse_gaps(q: CaseProfile, precs: list[Candidate], catalog: list[dict], statutory_now: dict[str, dict],
                 statutory_then: dict[str, set[str]], q_proc_regime: tuple[str, ...], s: GapSettings) -> list[dict]:
    """`q_proc_regime`: the procedural rule sets in force for the query (compared with each precedent's proc_regime)."""
    gaps = []
    demanded = any(e["code"] == "DEMAND_ISSUED" for e in q.events)
    for req in catalog:
        code = req["code"]
        qs = q.req_status.get(code)
        if qs not in OPEN:
            continue
        if req.get("category") == "office_process":
            continue  # the office's own action (site inspection, CTSO measurement): a next step, not a deficiency
        if req.get("category") == "payment" and not demanded:
            continue  # nothing to pay before the demand letter; the payment shows up under next steps
        applicable = [c for c in precs if c.profile.req_status.get(code) not in (None, "not_applicable")]
        w_total = sum(c.score for c in applicable)
        needed = [c for c in applicable if c.profile.req_status.get(code) in NEEDED or code in c.profile.req_queried]
        queried = [c for c in applicable if code in c.profile.req_queried]
        support = sum(c.score for c in needed) / w_total if w_total else None
        queried_share = sum(c.score for c in queried) / w_total if w_total else None
        statutory = code in statutory_now
        validators = [v for v in requirement_validators(code, req) if v in q.failed_validations]
        by_support = support is not None and len(applicable) >= s.min_support_cases and support >= s.support_threshold
        if not (statutory or validators or by_support):
            continue
        basis = label(statutory, [Support(c.score, c.proc_regime == q_proc_regime, code in statutory_then.get(c.profile.case_id, set()))
                                  for c in needed])
        cites = [x for x in (_precedent_cite(c, code) for c in sorted(needed, key=lambda c: -c.score)) if x]
        rule = statutory_now.get(code)
        gaps.append({
            "code": code, "name_en": req.get("name_en"), "name_mr": req.get("name_mr"), "category": req.get("category"),
            "catalog_status": req.get("status"),
            "query_status": qs, "kind": "invalid" if qs == "invalid" else qs,
            "basis": basis, "statutory": statutory,
            "reported_because": [r for r, ok in (("statutory", statutory), ("validator", bool(validators)),
                                                   ("precedent_support", by_support)) if ok],
            "support": round(support, 3) if support is not None else None,
            "queried_share": round(queried_share, 3) if queried_share is not None else None,
            "n_applicable": len(applicable), "n_supporting": len(needed), "n_queried": len(queried),
            "citations": cites[:MAX_CITES],
            "was_queried_in": [x for x in cites if x["how"] == "queried"][:MAX_CITES],
            "query_evidence": ([q.req_queried[code]] if code in q.req_queried else []),
            "validators": [{"code": v, "message": q.failed_validations[v]["message"], "evidence": q.validator_evidence(v)}
                           for v in validators],
            "rules": [{"rule_set_code": rule["rule_set_code"], "status": rule["status"], "source_ref": rule.get("source_ref"),
                       "mandatory": rule.get("mandatory", True), "applies": rule.get("applies")}] if rule else [],
        })
    order = {b: i for i, b in enumerate(LABELS)}
    gaps.sort(key=lambda g: (order[g["basis"]], g["kind"] != "invalid", -(g["support"] or 0), g["code"]))
    return gaps
