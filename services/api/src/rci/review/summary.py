"""Templated facts summary (Marathi + English) and the three embedding facet texts of an approved card.

Everything is built from structured card facts and codes, never from free page text, so the output is
deterministic and contains only redacted tokens where the card does.

Facets
  facts      who / where / what land / tenure / area / key dates
  documents  which exhibit types were filed at which stage, requirement coverage
  issues     query items with their requirement codes, failed validations, missing / invalid / queried requirements
"""
from __future__ import annotations

from typing import Any

from rci.pipeline.normalise import devanagari_digits

TENURE = {
    "occupancy_class_2": ("भोगवटादार वर्ग-२", "Occupancy Class-II"),
    "govt_lease": ("शासकीय भाडेपट्टा", "Government lease"),
    "occupancy_class_1": ("भोगवटादार वर्ग-१", "Occupancy Class-I"),
}
DECISION = {"granted": ("मंजूर", "granted"), "rejected": ("नामंजूर", "rejected")}


def _v(facts: dict, path: str) -> Any:
    group, _, name = path.partition(".")
    f = (facts.get(group) or {}).get(name)
    return f.get("value") if isinstance(f, dict) else None


def _mr(v: Any) -> str:
    return devanagari_digits(str(v))


def summary(card: dict) -> dict:
    """{"mr": ..., "en": ...}"""
    f = card["facts"]
    soc = _v(f, "society.name") or "—"
    village, taluka, district = _v(f, "land.village"), _v(f, "land.taluka"), _v(f, "land.district")
    cts, area, tenure = _v(f, "land.cts_no"), _v(f, "land.area_sqm"), _v(f, "land.tenure")
    app_date, order_date, decision = _v(f, "application.date"), _v(f, "order.order_date"), _v(f, "order.decision")
    premium = _v(f, "order.premium_amount") or _v(f, "demand.amount")
    t_mr, t_en = TENURE.get(tenure, ("—", "—"))

    mr, en = [], []
    place_mr = ", ".join(x for x in [f"मौजे {village}" if village else None, f"ता. {taluka}" if taluka else None,
                                    f"जि. {district}" if district else None] if x)
    place_en = ", ".join(x for x in [village, f"Tal. {taluka}" if taluka else None, f"Dist. {district}" if district else None] if x)
    mr.append(f"अर्जदार: {soc}.")
    en.append(f"Applicant: {soc}.")
    if place_mr or cts:
        mr.append(f"जमीन: {place_mr}{'; ' if place_mr and cts else ''}{'न.भू.क्र. ' + _mr(cts) if cts else ''}"
                  f"{', क्षेत्र ' + _mr(area) + ' चौ.मी.' if area else ''}.")
        en.append(f"Land: {place_en}{'; ' if place_en and cts else ''}{'CTS ' + str(cts) if cts else ''}"
                  f"{', area ' + str(area) + ' sq m' if area else ''}.")
    mr.append(f"सध्याचा धारणाधिकार: {t_mr}; मागणी: भोगवटादार वर्ग-१ मध्ये रूपांतरण.")
    en.append(f"Current tenure: {t_en}; sought: conversion to Occupancy Class-I.")
    if app_date:
        mr.append(f"अर्ज दिनांक {_mr(app_date)}.")
        en.append(f"Application dated {app_date}.")
    nq = len(f.get("queries") or [])
    if nq:
        items = sum(len(q["items"]) for q in f["queries"])
        mr.append(f"त्रुटी पत्रे: {_mr(nq)} ({_mr(items)} मुद्दे).")
        en.append(f"Query letters: {nq} ({items} items).")
    if f.get("inspections"):
        mr.append(f"स्थळ पाहणी / मोजणी अहवाल: {_mr(len(f['inspections']))}.")
        en.append(f"Inspection / measurement reports: {len(f['inspections'])}.")
    if premium:
        mr.append(f"अधिमूल्य / मागणी रक्कम रु. {_mr(premium)}.")
        en.append(f"Premium / demand amount Rs. {premium}.")
    if order_date or decision:
        d_mr, d_en = DECISION.get(decision, ("—", "—"))
        mr.append(f"जिल्हाधिकारी आदेश{' दिनांक ' + _mr(order_date) if order_date else ''}: {d_mr}.")
        en.append(f"Collector order{' dated ' + order_date if order_date else ''}: {d_en}.")
    tidy = lambda parts: " ".join(x.replace("..", ".") for x in parts)  # noqa: E731 - "चौ.मी." + "." at sentence end
    return {"mr": tidy(mr), "en": tidy(en)}


def facet_texts(card: dict, names: dict[str, dict] | None = None) -> dict[str, str]:
    """`names` maps exhibit-type / requirement codes to {"name_mr", "name_en"} so facets carry both languages."""
    names = names or {}

    def name(code: str) -> str:
        n = names.get(code) or {}
        return f"{code} {n.get('name_mr', '')} {n.get('name_en', '')}".strip()

    f = card["facts"]
    s = card.get("summary") or summary(card)
    facts_text = "\n".join([
        s["mr"], s["en"],
        f"tenure={_v(f, 'land.tenure')} village={_v(f, 'land.village')} taluka={_v(f, 'land.taluka')} "
        f"district={_v(f, 'land.district')} area_sqm={_v(f, 'land.area_sqm')} lease_years={_v(f, 'lease.lease_period_years')} "
        f"decision={_v(f, 'order.decision')}",
    ])
    docs = [f"{e['stage']}: {name(e['type'])}" for e in card["exhibits"]]
    reqs = [f"{r['code']}={r['status']}" for r in card["requirements"]]
    documents_text = "\n".join(docs + ["requirements: " + " ".join(reqs)])
    issues = []
    for q in f.get("queries") or []:
        for it in q["items"]:
            codes = it.get("requirement_codes")
            label = "not_requirement" if it.get("mapping") == "not_requirement" else " ".join(codes or [])
            issues.append(f"query [{label}]: {it.get('value') or it.get('raw')}")
    issues += [f"validation {v['code']} failed: {v['message']}" for v in card["validations"] if v["status"] == "fail"]
    issues += [f"requirement {name(r['code'])}: {r['status']}" for r in card["requirements"]
               if r["status"] in ("missing", "invalid", "queried")]
    issues_text = "\n".join(issues) or "no issues recorded"
    return {"facts": facts_text, "documents": documents_text, "issues": issues_text}
