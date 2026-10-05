"""Stage assignment for exhibits.

  filing          what the society submitted with the application
  query_response  what the society submitted after a query letter (reply + enclosures)
  internal        office-generated working papers (query/demand letters, site & CTSO reports, notes)
  final           the Collector's order and payments made after the demand/order

"When was it submitted" follows docket position (enclosures keep their own, older dates), so dates are
only used to detect a docket filed in reverse order (latest paper on top).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass
class StageInput:
    exhibit_type: str
    party: str  # applicant | office
    default_stage: str
    doc_date: dt.date | None = None


def _is_reversed(items: list[StageInput]) -> bool:
    dated = [(i, x.doc_date) for i, x in enumerate(items) if x.doc_date and x.exhibit_type in
             {"APPLICATION", "QUERY_LETTER", "QUERY_REPLY", "DEMAND_LETTER", "COLLECTOR_ORDER"}]
    if len(dated) < 2:
        return False
    ascending = sum(1 for (i, a), (j, b) in zip(dated, dated[1:]) if b >= a)
    return ascending < (len(dated) - 1) / 2


def assign_stages(items: list[StageInput]) -> list[str]:
    order = list(range(len(items)))
    if _is_reversed(items):
        order = order[::-1]
    pos = {idx: rank for rank, idx in enumerate(order)}

    def first(t: str) -> int | None:
        ranks = [pos[i] for i, x in enumerate(items) if x.exhibit_type == t]
        return min(ranks) if ranks else None

    q, demand, final_order = first("QUERY_LETTER"), first("DEMAND_LETTER"), first("COLLECTOR_ORDER")
    stages = []
    for i, x in enumerate(items):
        r = pos[i]
        after_query = q is not None and r > q
        if x.exhibit_type == "COLLECTOR_ORDER":
            stages.append("final")
        elif x.exhibit_type == "CHALLAN":
            if (demand is not None and r > demand) or (final_order is not None and r > final_order):
                stages.append("final")
            else:
                stages.append("query_response" if after_query else "filing")
        elif x.party == "office":
            stages.append("internal")
        elif x.exhibit_type == "QUERY_REPLY":
            stages.append("query_response")
        else:
            stages.append("query_response" if after_query else "filing")
    return stages
