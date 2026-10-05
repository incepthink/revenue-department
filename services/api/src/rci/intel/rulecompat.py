"""Rule-compatibility label of every recommendation (gap or next step).

  STATUTORY           the rule stack in force on the query's rule date requires it (a rule_requirements /
                      rule_obligations row that applies to the case's facts).
  SUPERSEDED          not required now, and most of its precedent support comes from cases decided under a rule
                      stack that did require it — the requirement has since been dropped or overridden.
  PRACTICE            not statutory; most of its support comes from precedents decided under the same rule stack
                      as the query (current office practice).
  PRACTICE_OLD_RULES  not statutory; most support comes from precedents decided under older rule stacks, so the
                      practice may not survive the rule change.

Regime here = the *procedural* rule sets in force on the rule date (those carrying requirement / obligation rows):
a notification that only changes premium rates does not make earlier practice "old".
"""
from __future__ import annotations

from dataclasses import dataclass

STATUTORY, PRACTICE, PRACTICE_OLD_RULES, SUPERSEDED = "STATUTORY", "PRACTICE", "PRACTICE_OLD_RULES", "SUPERSEDED"
LABELS = (STATUTORY, PRACTICE, PRACTICE_OLD_RULES, SUPERSEDED)
LABEL_TEXT = {
    STATUTORY: ("Required by the rules in force", "सध्या लागू नियमांनुसार आवश्यक"),
    PRACTICE: ("Office practice under the current rules", "सध्याच्या नियमांखालील कार्यालयीन प्रथा"),
    PRACTICE_OLD_RULES: ("Office practice under older rules", "जुन्या नियमांखालील कार्यालयीन प्रथा"),
    SUPERSEDED: ("Required only under superseded rules", "केवळ रद्द / बदललेल्या नियमांनुसार आवश्यक"),
}


@dataclass(frozen=True)
class Support:
    weight: float
    same_regime: bool  # precedent decided under the query's rule stack
    statutory_then: bool  # the precedent's own rule stack required it


def label(statutory_now: bool, supports: list[Support]) -> str:
    if statutory_now:
        return STATUTORY
    total = sum(s.weight for s in supports)
    if total <= 0:
        return PRACTICE
    if sum(s.weight for s in supports if s.statutory_then) / total >= 0.5:
        return SUPERSEDED
    return PRACTICE if sum(s.weight for s in supports if s.same_regime) / total >= 0.5 else PRACTICE_OLD_RULES


def regime_similarity(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    if not a and not b:
        return 1.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)
