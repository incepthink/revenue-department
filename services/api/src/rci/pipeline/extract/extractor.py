"""Per-exhibit extraction with evidence.

1. Rules extractor (always): deterministic regexes over the redacted text → Evidence.
2. LLM extractor (when a provider is configured and allowed for the case's data class): strict JSON
   schema asking for the *verbatim* raw text + page number per field; values are parsed by the same
   parsers as the rules. An LLM answer whose raw text is not found on the cited page is discarded
   (kept under `unverified`), so every accepted value is grounded in a page.
3. Merge: agreement → confidence 0.95; disagreement → verified LLM wins, rules value kept as an
   alternative for the reviewer.
"""
from __future__ import annotations

import logging
from typing import Any

from rci.adapters.llm import DataClass, LLMClient, LLMError
from rci.pipeline.extract.common import Evidence, PageText, jsonable, locate, make_evidence
from rci.pipeline.extract.fields import FIELDS, FieldSpec, parse_value

log = logging.getLogger(__name__)

EXTRACTION_SYSTEM = (
    "You extract facts from one exhibit of a Maharashtra revenue-department docket (CHS land conversion from "
    "Occupancy Class-II / Government lease to Class-I). Text is Marathi and/or English; personal data is "
    "replaced by tokens like [PERSON_ab12cd34] — copy tokens as-is, never guess the underlying value. "
    "For each field copy `raw` VERBATIM from the page (same digits, same script) and give its page_seq. "
    "If a field is not stated, set found=false and leave raw/snippet empty. Never infer or compute values."
)


def _field_schema(spec: FieldSpec) -> dict:
    item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["found", "raw", "page_seq"],
        "properties": {
            "found": {"type": "boolean"},
            "raw": {"type": "string", "description": spec.description},
            "page_seq": {"type": "integer"},
        },
    }
    if spec.kind == "list":
        return {"type": "array", "description": spec.description, "items": item}
    return item


def extraction_schema(exhibit_type: str) -> dict:
    specs = FIELDS[exhibit_type]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"extraction/{exhibit_type}.v1",
        "title": f"{exhibit_type} extraction",
        "type": "object",
        "additionalProperties": False,
        "required": [s.name for s in specs],
        "properties": {s.name: _field_schema(s) for s in specs},
    }


def _strip_meta(schema: dict) -> dict:
    return {k: v for k, v in schema.items() if not k.startswith("$") and k != "title"}


def rules_extract(exhibit_type: str, pages: list[PageText]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for spec in FIELDS.get(exhibit_type, []):
        if spec.rule is None:
            continue
        try:
            out[spec.name] = spec.rule(pages)
        except Exception as e:  # a broken rule must not kill the exhibit
            log.exception("rule %s.%s failed: %s", exhibit_type, spec.name, e)
            out[spec.name] = None
    return out


_locate = locate  # moved to common; the old name stays importable


def _llm_evidence(spec: FieldSpec, item: dict, by_seq: dict[int, PageText]) -> tuple[Evidence | None, dict | None]:
    if not item.get("found") or not item.get("raw", "").strip():
        return None, None
    page = by_seq.get(item["page_seq"])
    span = _locate(item["raw"], page) if page else None
    if span is None:
        # try all pages of the exhibit before giving up
        for p in by_seq.values():
            span = _locate(item["raw"], p)
            if span:
                page = p
                break
    if span is None or page is None:
        return None, {"field": spec.name, "raw": item["raw"], "page_seq": item["page_seq"], "reason": "raw text not on page"}
    value = parse_value(spec.kind, page.text[span[0] : span[1]], spec.enum)
    if value in (None, ""):
        return None, {"field": spec.name, "raw": item["raw"], "reason": "unparseable"}
    return make_evidence(page, span[0], span[1], value, 0.8, method="llm"), None


def llm_extract(exhibit_type: str, pages: list[PageText], llm: LLMClient, data_class: DataClass) -> tuple[dict[str, Any], list[dict]]:
    schema = _strip_meta(extraction_schema(exhibit_type))
    user = "\n\n".join(f"=== page_seq {p.seq} ===\n{p.text}" for p in pages)[:24000]
    out = llm.complete_json(
        task=f"extract_{exhibit_type}", system=EXTRACTION_SYSTEM, user=user, schema=schema,
        schema_name=f"extract_{exhibit_type.lower()}", data_class=data_class,
    )
    by_seq = {p.seq: p for p in pages}
    result: dict[str, Any] = {}
    unverified: list[dict] = []
    for spec in FIELDS[exhibit_type]:
        val = out.get(spec.name)
        if spec.kind == "list":
            evs = []
            for item in val or []:
                ev, bad = _llm_evidence(FieldSpec(spec.name, "text", spec.description), item, by_seq)
                if ev:
                    evs.append(ev)
                if bad:
                    unverified.append(bad)
            result[spec.name] = evs
        else:
            ev, bad = _llm_evidence(spec, val or {}, by_seq)
            result[spec.name] = ev
            if bad:
                unverified.append(bad)
    return result, unverified


def _same(a: Any, b: Any) -> bool:
    return jsonable(a) == jsonable(b)


def merge(rules: dict[str, Any], llm: dict[str, Any] | None) -> dict[str, Any]:
    if llm is None:
        return rules
    merged: dict[str, Any] = {}
    for name in set(rules) | set(llm):
        r, l = rules.get(name), llm.get(name)
        if isinstance(r, list) or isinstance(l, list):
            merged[name] = l if l else (r or [])
            continue
        if r and l:
            if _same(r.value, l.value):
                l.method, l.confidence = "rules+llm", 0.95
                merged[name] = l
            else:
                l.alternatives.append({"value": jsonable(r.value), "raw": r.raw, "page_seq": r.page_seq, "method": "rules"})
                l.confidence = 0.6
                merged[name] = l
        else:
            merged[name] = l or r
    return merged


def extract_exhibit(
    exhibit_type: str,
    pages: list[PageText],
    *,
    llm: LLMClient | None = None,
    data_class: DataClass = "synthetic",
) -> dict[str, Any]:
    """Returns {"fields": {name: evidence|[evidence]|None}, "methods": [...], "unverified": [...]}"""
    if exhibit_type not in FIELDS:
        return {"fields": {}, "methods": [], "unverified": []}
    rules = rules_extract(exhibit_type, pages)
    methods, unverified, llm_out = ["rules"], [], None
    if llm is not None and llm.available:
        try:
            llm_out, unverified = llm_extract(exhibit_type, pages, llm, data_class)
            methods.append("llm")
        except LLMError as e:
            log.warning("LLM extraction skipped for %s: %s", exhibit_type, e)
    merged = merge(rules, llm_out)
    fields = {}
    for spec in FIELDS[exhibit_type]:
        v = merged.get(spec.name)
        if isinstance(v, list):
            fields[spec.name] = [e.to_json() for e in v]
        else:
            fields[spec.name] = v.to_json() if v else None
    return {"fields": fields, "methods": methods, "unverified": unverified}
