"""Narrative slots: the Marathi recital of the case (scrutiny note "हकीकत", order "वस्तुस्थिती").

The template recital is built from structured card facts only and is always computed. When an LLM is configured it may
re-word it (the client's data-class gate applies), under two checks; if either
fails the template stays and the draft records why:
  1. cross-check   every number / date / ID in the output is a fact of the case (crosscheck.check)
  2. completeness  every date and number of the template recital is still present (nothing dropped or changed)
The LLM sees the template recital and the facts it was built from — never page text or PII tokens.
"""
from __future__ import annotations

import json
import logging

from rci.adapters.llm import LLMClient, LLMError
from rci.drafting import crosscheck
from rci.drafting.mrtext import inr, mr_date, mr_num
from rci.pipeline.redact.vault import TOKEN_RE
from rci.review.summary import TENURE

log = logging.getLogger(__name__)

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["text_mr"], "properties": {"text_mr": {"type": "string"}}}
SYSTEM = (
    "तुम्ही जिल्हाधिकारी कार्यालयातील आदेश / टिप्पणीसाठी प्रकरणाची वस्तुस्थिती औपचारिक मराठीत लिहिता. Rewrite the given draft "
    "recital as fluent, formal Marathi prose. Use ONLY the facts given; keep every date, amount, area and number exactly as "
    "written (Devanagari digits); do not add any fact, number, date, name or reference. Return JSON only."
)


def _v(card: dict, path: str):
    group, _, name = path.partition(".")
    f = (card["facts"].get(group) or {}).get(name)
    return f.get("value") if isinstance(f, dict) else None


def facts_for(card: dict) -> dict:
    f = card["facts"]
    queries = sorted(f.get("queries") or [], key=lambda q: ((q.get("letter_date") or {}).get("value") or ""))
    insp = [i for i in f.get("inspections") or [] if i["kind"] == "FIELD_REPORT"]
    ctso = [i for i in f.get("inspections") or [] if i["kind"] == "CTSO_REPORT"]
    pays = [p for p in f.get("payments") or [] if (p.get("amount") or {}).get("value")]
    val = lambda d, k: (d.get(k) or {}).get("value") if d else None  # noqa: E731
    reply = next((e.get("date") for e in card.get("events") or [] if e["code"] == "QUERY_REPLIED" and e.get("date")), None)
    return {
        "application_date": _v(card, "application.date"), "village": _v(card, "land.village"), "cts_no": _v(card, "land.cts_no"),
        "area_sqm": _v(card, "land.area_sqm"), "tenure": _v(card, "land.tenure"),
        "query_date": val(queries[0], "letter_date") if queries else None, "query_count": len(queries),
        "query_items": len(queries[0]["items"]) if queries else 0, "reply_date": reply,
        "inspection_date": val(insp[-1], "inspection_date") if insp else None,
        "inspection_area": val(insp[-1], "area_sqm") if insp else None,
        "encroachment": val(insp[-1], "encroachment") if insp else None,
        "ctso_date": val(ctso[-1], "inspection_date") if ctso else None, "ctso_area": val(ctso[-1], "area_sqm") if ctso else None,
        "demand_date": _v(card, "demand.letter_date"), "demand_amount": _v(card, "demand.amount"),
        "payment_date": val(pays[-1], "payment_date") if pays else None, "paid_amount": val(pays[-1], "amount") if pays else None,
        "grn": val(pays[-1], "grn") if pays else None,
    }


def template(card: dict, *, include_application: bool = True) -> str:
    """Land sentence first, then the events in date order."""
    x = facts_for(card)
    head, events = [], []  # events: (iso date, sentence)
    if x["village"] or x["area_sqm"]:
        t_mr = TENURE.get(x["tenure"], ("—", "—"))[0]
        head.append(f"अभिलेखानुसार मौजे {x['village'] or '—'} येथील नगर भूमापन क्रमांक {mr_num(x['cts_no'] or '—')} चे क्षेत्र "
                    f"{mr_num(x['area_sqm'] or '—')} चौ.मी. असून सध्याचा धारणाधिकार {t_mr} आहे.")
    if include_application and x["application_date"]:
        head.insert(0, f"संस्थेने दिनांक {mr_date(x['application_date'])} रोजी भोगवटादार वर्ग-१ मध्ये रूपांतरणासाठी अर्ज सादर केला आहे.")
    if x["query_date"]:
        events.append((x["query_date"], f"या कार्यालयाने दिनांक {mr_date(x['query_date'])} रोजी त्रुटी पत्र पाठविले असून त्यामध्ये "
                                        f"{mr_num(x['query_items'])} मुद्दे नमूद केले होते."))
    if x["reply_date"]:
        events.append((x["reply_date"], f"संस्थेने दिनांक {mr_date(x['reply_date'])} रोजी त्रुटींची पूर्तता केली आहे."))
    if x["inspection_date"]:
        s = f"दिनांक {mr_date(x['inspection_date'])} रोजी स्थळ पाहणी करण्यात आली"
        s += f" असून प्रत्यक्ष क्षेत्र {mr_num(x['inspection_area'])} चौ.मी. आढळले आहे" if x["inspection_area"] else ""
        s += "; जागेवर अतिक्रमण आढळून आले आहे." if x["encroachment"] is True else (
            "; जागेवर अतिक्रमण आढळून आलेले नाही." if x["encroachment"] is False else ".")
        events.append((x["inspection_date"], s))
    if x["ctso_date"]:
        events.append((x["ctso_date"], f"नगर भूमापन अधिकारी यांनी दिनांक {mr_date(x['ctso_date'])} रोजी मोजणी केली असून क्षेत्र "
                                       f"{mr_num(x['ctso_area'] or '—')} चौ.मी. आहे."))
    if x["demand_date"]:
        amt = f" रु. {mr_num(inr(x['demand_amount']))}/-" if x["demand_amount"] else ""
        events.append((x["demand_date"], f"या कार्यालयाने दिनांक {mr_date(x['demand_date'])} रोजी{amt} नजराणा रकमेचे मागणी पत्र "
                                         "पाठविले आहे."))
    if x["payment_date"]:
        grn = f" (GRN {x['grn']})" if x["grn"] else ""
        events.append((x["payment_date"], f"संस्थेने दिनांक {mr_date(x['payment_date'])} रोजी रु. {mr_num(inr(x['paid_amount']))}/- "
                                          f"चलनाद्वारे{grn} शासन जमा केले आहेत."))
    return " ".join(head + [s for _, s in sorted(events, key=lambda e: str(e[0]))])


def _figures(text: str) -> set[str]:
    from rci.pipeline.normalise import ascii_digits

    t = ascii_digits(text)
    dates = {"/".join(m) for m in crosscheck.DATE_DMY.findall(t)}
    return dates | {m.replace(",", "") for m in crosscheck.NUMBER.findall(crosscheck.DATE_DMY.sub(" ", t))}


def narrative(card: dict, *, fee: dict | None, llm: LLMClient | None, data_class: str, include_application: bool = True,
              extra_allowed: list | None = None) -> dict:
    """{"text", "source": template|llm, "check": None|reason, "violations": [...]}"""
    base = template(card, include_application=include_application)
    x = facts_for(card)
    allowed = crosscheck.allowed_from(card, fee=fee, extra=[x["query_items"], x["query_count"], *(extra_allowed or [])])
    base_violations = crosscheck.check(base, allowed)
    out = {"text": base, "source": "template", "check": None, "violations": base_violations}
    if llm is None or not llm.available or not base:
        out["check"] = None if llm is None else "no LLM configured"
        return out
    try:
        res = llm.complete_json(task="draft_narrative", system=SYSTEM,
                                user=json.dumps({"draft_recital": base, "facts": x}, ensure_ascii=False, default=str),
                                schema=SCHEMA, schema_name="draft_narrative", data_class=data_class)
    except LLMError as e:
        log.warning("narrative LLM failed: %s", e)
        out["check"] = f"llm error: {e}"
        return out
    text = (res.get("text_mr") or "").strip()
    problems = crosscheck.check(text, allowed)
    if not text:
        out["check"] = "rejected: empty text"
    elif TOKEN_RE.search(text):
        out["check"] = "rejected: contains a PII token"
    elif problems:
        out["check"] = f"rejected: untraceable figures {[p['value'] for p in problems]}"
    elif _figures(base) - _figures(text):
        out["check"] = f"rejected: dropped figures {sorted(_figures(base) - _figures(text))}"
    else:
        out.update(text=text, source="llm", check="passed", violations=[])
    return out
