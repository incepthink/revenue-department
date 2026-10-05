"""Intake scrutiny of one uploaded document: one LLM call per file.

The model is told which document the applicant said this is and returns, in one strict JSON answer:
  * whether the text really is that document (and what it looks like otherwise),
  * the slot's fields — `raw` copied verbatim with its page, grounded exactly like the pipeline extractor (a value
    whose raw text is not on the page is dropped and listed under `unverified`),
  * problems a scrutiny clerk would raise about this document on its own (severity + page),
  * a verdict on each rule in force that says what this document must show (rule_document_checks), with a verbatim
    quote — a met / not_met verdict whose quote is not on the page becomes cannot_tell (listed under `unverified`).
For an additional document (slots.OTHER) the model says what it is and whether it concerns this application at all
(summary, relevance) instead of whether it matches a slot, and which required document without a slot of its own
(e.g. the general body resolution) it is, if any.
The input is the redacted page text, never the image, so no PII reaches the model. Without an LLM (or when the
provider is not allowed for the case's data class) the pipeline's rules extractor fills what it can.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from rci.adapters.llm import DataClass, LLMClient, LLMError
from rci.intake.slots import OTHER, Slot, SlotField
from rci.pipeline.extract.common import Evidence, PageText, snippet
from rci.pipeline.extract.extractor import _llm_evidence, _locate, merge
from rci.pipeline.extract.fields import TENURE_PATTERNS, FieldSpec
from rci.pipeline.normalise import ascii_digits

log = logging.getLogger(__name__)

MAX_CHARS = 24000
MIN_TEXT_CHARS = 20  # fewer readable characters than this (tokens and whitespace aside) = nothing to check

SYSTEM = (
    "You scrutinise one document filed with an application to the District Collector (Maharashtra revenue "
    "department) by a cooperative housing society to convert its land from Occupancy Class-II / Government lease "
    "to Occupancy Class-I. {origin} Text is Marathi and/or English; personal "
    "data is replaced by tokens like [PERSON_ab12cd34] — copy tokens as-is, never guess the underlying value.\n"
    "1. document_matches: is this text really that kind of document? detected_as: what it actually is, in a few "
    "words. match_reason: one short sentence.\n"
    "2. fields: copy `raw` VERBATIM from the page (same digits, same script) with its page_seq. If a field is not "
    "stated, set found=false and raw=\"\". Never infer, translate or compute values.\n"
    "3. observations: concrete issues a scrutiny clerk would raise about THIS document alone — missing signature, "
    "seal or date; illegible, cut-off or missing pages; entries that contradict each other inside the document; "
    "encumbrances, restrictions or dues recorded on it. severity: problem = must be fixed before the case can "
    "proceed, warning = the officer should look at it, info = worth knowing. Give the page_seq (0 if none). Write "
    "each in one short English sentence. Do not comment on other documents and do not invent issues: an empty "
    "list is the right answer for a document in order."
)
OTHER_TASK = (
    "\n4. This is not one of the standard filing documents: set document_matches to true only when it is a legible "
    "document at all, and say in detected_as what it is. summary: what it is and what it says that matters for this "
    "conversion application, in one or two short English sentences. relevance: supporting = it concerns this society, "
    "its land or this application (registration certificate, general body resolution, member list, NOC, conveyance "
    "deed, building permission, correspondence with the office, …); unrelated = it has nothing to do with them."
)

RULES_TASK = (
    "\n{n}. rule_results: check THIS document against each rule below, one answer per rule_code. verdict: met = the "
    "document shows the rule is satisfied; not_met = it shows the rule is not satisfied; cannot_tell = the text does not "
    "let you decide; not_applicable = the rule does not concern this document. quote: the words your verdict rests on, "
    "copied VERBATIM from the page (same digits, same script), with its page_seq — when something required is missing, "
    "quote the place where it should be (e.g. the closing lines where a signature belongs). For cannot_tell and "
    "not_applicable leave quote empty and page_seq 0. reason: one short English sentence.\nRULES TO CHECK:\n{rules}"
)
REQUIREMENTS_TASK = (
    "\n{n}. satisfies_requirement: if this document is one of these required documents, give its code; otherwise "
    "null.\n{requirements}"
)
VERDICTS = ("met", "not_met", "cannot_tell", "not_applicable")


def system_prompt(slot: Slot, identified: bool, checks: list[dict] = (), requirements: list[dict] = ()) -> str:
    """`checks`: the rule_document_checks in force for this slot (code, text_en). `requirements`: for an additional
    document, the required documents that have no slot of their own (code, name_en, name_mr)."""
    origin = (f"The file was identified automatically as {slot.what}." if identified
              else f"The applicant uploaded this file as {slot.what}.")
    out = SYSTEM.format(origin=origin)
    n = 4
    if slot.key == OTHER.key:
        out += OTHER_TASK
        n += 1
    if checks:
        rules = "\n".join(f"{i}. [{c['code']}] {c['text_en']}" for i, c in enumerate(checks, 1))
        out += RULES_TASK.format(n=n, rules=rules)
        n += 1
    if slot.key == OTHER.key and requirements:
        listed = "\n".join(f"- {r['code']}: {r['name_en']}" + (f" ({r['name_mr']})" if r.get("name_mr") else "")
                            for r in requirements)
        out += REQUIREMENTS_TASK.format(n=n, requirements=listed)
    return out


def _field_item(f: SlotField) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["found", "raw", "page_seq"],
        "properties": {
            "found": {"type": "boolean"},
            "raw": {"type": "string", "description": f.description},
            "page_seq": {"type": "integer"},
        },
    }


def scrutiny_schema(slot: Slot, checks: list[dict] = (), requirements: list[dict] = ()) -> dict:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["document_matches", "detected_as", "match_reason", "fields", "observations"],
        "properties": {
            "document_matches": {"type": "boolean"},
            "detected_as": {"type": "string"},
            "match_reason": {"type": "string"},
            "fields": {
                "type": "object",
                "additionalProperties": False,
                "required": [f.name for f in slot.fields],
                "properties": {f.name: _field_item(f) for f in slot.fields},
            },
            "observations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["severity", "text", "page_seq"],
                    "properties": {
                        "severity": {"type": "string", "enum": ["problem", "warning", "info"]},
                        "text": {"type": "string"},
                        "page_seq": {"type": "integer"},
                    },
                },
            },
        },
    }
    if slot.key == OTHER.key:
        schema["required"] += ["summary", "relevance"]
        schema["properties"]["summary"] = {"type": "string"}
        schema["properties"]["relevance"] = {"type": "string", "enum": ["supporting", "unrelated"]}
        if requirements:
            schema["required"].append("satisfies_requirement")
            schema["properties"]["satisfies_requirement"] = {"type": ["string", "null"],
                                                             "enum": [r["code"] for r in requirements] + [None]}
    if checks:
        schema["required"].append("rule_results")
        schema["properties"]["rule_results"] = {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rule_code", "verdict", "quote", "page_seq", "reason"],
                "properties": {
                    "rule_code": {"type": "string", "enum": [c["code"] for c in checks]},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "quote": {"type": "string"},
                    "page_seq": {"type": "integer"},
                    "reason": {"type": "string"},
                },
            },
        }
    return schema


def tenure_of(raw: str) -> str | None:
    s = ascii_digits(raw or "")
    return next((val for rx, val in TENURE_PATTERNS if rx.search(s)), None)


def readable(pages: list[PageText]) -> bool:
    text = "".join(re.sub(r"\[[A-Z_]+_[0-9a-f]{8}\]|\s+", "", p.text) for p in pages)
    return len(text) >= MIN_TEXT_CHARS


def _ground(f: SlotField, item: dict, by_seq: dict[int, PageText]) -> tuple[Evidence | None, dict | None]:
    kind = "text" if f.kind == "tenure" else f.kind
    ev, bad = _llm_evidence(FieldSpec(f.name, kind, f.description), item, by_seq)
    if ev is not None and f.kind == "tenure":
        # keep the verbatim text when it is not one of the known tenures; checks act on known codes only
        ev.value = tenure_of(ev.raw) or ev.value
    return ev, bad


def _not_checked(checks: list[dict], reason: str) -> list[dict]:
    return [{"code": c["code"], "text": c["text_en"], "verdict": "cannot_tell", "quote": None, "page_seq": None,
             "snippet": None, "reason": reason} for c in checks]


def _rule_results(checks: list[dict], answers: list[dict], by_seq: dict[int, PageText]) -> tuple[list[dict], list[dict]]:
    """(one result per check, unverified answers). A met / not_met verdict must rest on a quote found on the pages
    (the cited page first, like the field grounding); otherwise it becomes cannot_tell."""
    given: dict[str, dict] = {}
    for a in answers:
        given.setdefault(a.get("rule_code"), a)  # the first answer per rule counts; unknown codes are dropped
    results, unverified = [], []
    for c in checks:
        a = given.get(c["code"])
        if a is None:
            results += _not_checked([c], "the AI reader gave no answer for this rule")
            continue
        verdict = a["verdict"] if a.get("verdict") in VERDICTS else "cannot_tell"
        reason = (a.get("reason") or "").strip()[:400] or None
        quote, page, span = (a.get("quote") or "").strip(), None, None
        if quote:
            page = by_seq.get(a.get("page_seq"))
            span = _locate(quote, page) if page else None
            if span is None:
                page, span = next(((p, sp) for p in by_seq.values() if (sp := _locate(quote, p))), (None, None))
        if span is None and verdict in ("met", "not_met"):
            unverified.append({"field": f"rule:{c['code']}", "raw": quote, "page_seq": a.get("page_seq"),
                               "reason": "quote not on page" if quote else "no quote"})
            verdict, reason = "cannot_tell", f"the AI reader's answer ({a['verdict']}) could not be found on the page"
        grounded = span is not None
        results.append({"code": c["code"], "text": c["text_en"], "verdict": verdict,
                        "quote": page.text[span[0]:span[1]].strip() if grounded else None,
                        "page_seq": page.seq if grounded else None,
                        "snippet": snippet(page, span[0], span[1]) if grounded else None, "reason": reason})
    return results, unverified


def _llm_scrutiny(slot: Slot, pages: list[PageText], llm: LLMClient, data_class: DataClass, identified: bool,
                  checks: list[dict], requirements: list[dict]) -> tuple[dict, dict[str, Evidence | None], list[dict], list[dict]]:
    """(document judgement incl. rule results, grounded fields, observations, unverified answers)"""
    user = "\n\n".join(f"=== page_seq {p.seq} ===\n{p.text}" for p in pages)[:MAX_CHARS]
    out = llm.complete_json(
        task=f"intake_{slot.key}", system=system_prompt(slot, identified, checks, requirements), user=user,
        schema=scrutiny_schema(slot, checks, requirements), schema_name=f"intake_{slot.key}", data_class=data_class,
    )
    by_seq = {p.seq: p for p in pages}
    fields: dict[str, Evidence | None] = {}
    unverified: list[dict] = []
    for f in slot.fields:
        ev, bad = _ground(f, (out.get("fields") or {}).get(f.name) or {}, by_seq)
        fields[f.name] = ev
        if bad:
            unverified.append(bad)
    observations = [
        {"severity": o["severity"], "text": o["text"].strip()[:400], "page_seq": o["page_seq"] if o["page_seq"] in by_seq else None}
        for o in out.get("observations") or [] if o.get("text", "").strip()
    ]
    judgement = {"document_matches": bool(out["document_matches"]), "detected_as": out["detected_as"].strip()[:120] or None,
                 "match_reason": out["match_reason"].strip()[:400] or None}
    if slot.key == OTHER.key:
        judgement.update(summary=out["summary"].strip()[:600] or None, relevance=out["relevance"])
        offered = {r["code"] for r in requirements}
        judgement["satisfies_requirement"] = out.get("satisfies_requirement") if out.get("satisfies_requirement") in offered else None
    judgement["rule_results"], bad = _rule_results(checks, out.get("rule_results") or [], by_seq)
    return judgement, fields, observations, unverified + bad


def _rules(slot: Slot, pages: list[PageText]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for spec in slot.rule_specs():
        try:
            out[spec.name] = spec.rule(pages)
        except Exception:  # a broken rule must not stop the intake
            log.exception("intake rule %s.%s failed", slot.key, spec.name)
            out[spec.name] = None
    return out


def scrutinise_document(slot: Slot, pages: list[PageText], *, llm: LLMClient | None, data_class: DataClass,
                        unreadable_reason: str | None = None, identified: bool = False, checks: list[dict] = (),
                        requirements: list[dict] = ()) -> dict:
    """The stored per-document result (see rci.intake.checks for how it is used). `identified`: the slot was decided
    by rci.intake.identify, not by the applicant. `checks` / `requirements`: see system_prompt."""
    checks = list(checks)
    requirements = list(requirements) if slot.key == OTHER.key else []
    result: dict[str, Any] = {
        "slot": slot.key, "method": "none", "readable": readable(pages), "pages": [p.seq for p in pages],
        "document_matches": None, "detected_as": None, "match_reason": None,
        "fields": {f.name: None for f in slot.fields}, "observations": [], "unverified": [], "notes": [],
        "rule_results": _not_checked(checks, "not checked: the document was not read by the AI reader"),
    }
    if slot.key == OTHER.key:
        result.update(summary=None, relevance=None, satisfies_requirement=None)
    if not result["readable"]:
        result["notes"].append(unreadable_reason or "no text could be read from this file")
        return result
    rules = _rules(slot, pages)
    llm_fields = None
    if llm is not None and llm.available:
        try:
            judgement, llm_fields, observations, unverified = _llm_scrutiny(slot, pages, llm, data_class, identified,
                                                                            checks, requirements)
            result.update(judgement, observations=observations, unverified=unverified)
        except LLMError as e:
            log.warning("intake LLM skipped for %s: %s", slot.key, e)
            result["notes"].append(f"automatic reading unavailable ({e}); only pattern rules were applied")
    else:
        result["notes"].append("no LLM configured; only pattern rules were applied")
    result["method"] = "llm" if llm_fields is not None else "rules"
    if checks and llm_fields is None:
        result["notes"].append(f"{len(checks)} rule{'s' if len(checks) != 1 else ''} in force for this document could "
                               "not be checked without the AI reader")
    found_by_llm = {k: v for k, v in llm_fields.items() if v} if llm_fields is not None else None
    merged = merge({k: v for k, v in rules.items() if v}, found_by_llm)
    for f in slot.fields:
        ev = merged.get(f.name)
        result["fields"][f.name] = ev.to_json() if isinstance(ev, Evidence) else None
    return result
