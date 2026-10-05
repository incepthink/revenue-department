"""Marathi (and English) explanation of each gap.

The template is always computed. When an LLM is configured, it may re-word the Marathi text, under three checks;
if any fails, every gap keeps the template and the result records why:
  1. codes     the output lists exactly the input codes, in order (no gap added, dropped or renamed);
  2. no drift  a text mentions no requirement / validator code other than its own;
  3. numbers   every number in a text is one of that gap's own counts.
The LLM sees only codes, catalog names, statuses, labels and counts — never page text or PII.
"""
from __future__ import annotations

import json
import logging
import re

from rci.adapters.llm import LLMClient, LLMError
from rci.intel.rulecompat import LABEL_TEXT
from rci.pipeline.normalise import ascii_digits, devanagari_digits

log = logging.getLogger(__name__)

STATUS_TEXT = {
    "missing": ("not filed with the application", "अर्जासोबत सादर केलेले नाही"),
    "invalid": ("filed, but a check on it failed", "सादर केले आहे, परंतु तपासणीत त्रुटी आढळली"),
    "queried": ("already asked for in a query letter", "त्रुटी पत्राद्वारे यापूर्वीच मागवले आहे"),
}
CODE_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
NUM_RE = re.compile(r"\d+(?:\.\d+)?")
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["code", "text_mr"],
        "properties": {"code": {"type": "string"}, "text_mr": {"type": "string"}}}}},
}
SYSTEM = (
    "तुम्ही महसूल कार्यालयातील अधिकाऱ्यासाठी छाननी टिप्पणी लिहिता. For each item write ONE short, formal Marathi "
    "sentence explaining why the requirement is flagged. Use only the facts given in the item. Keep every item and "
    "its code exactly as given, in the same order; do not add items; do not mention any other code; use no numbers "
    "other than the item's own counts. Return JSON only."
)


def template(g: dict) -> tuple[str, str]:
    st_en, st_mr = STATUS_TEXT.get(g["query_status"], (g["query_status"], g["query_status"]))
    b_en, b_mr = LABEL_TEXT[g["basis"]]
    en = [f"{g['name_en']} ({g['code']}): {st_en}."]
    mr = [f"{g['name_mr']}: {st_mr}."]
    if g["n_applicable"]:
        en.append(f"Needed in {g['n_supporting']} of {g['n_applicable']} similar decided cases"
                  + (f", asked for by query letter in {g['n_queried']}" if g["n_queried"] else "") + ".")
        mr.append(f"{devanagari_digits(str(g['n_applicable']))} पैकी {devanagari_digits(str(g['n_supporting']))} समान निर्णीत "
                  f"प्रकरणांत ही बाब आवश्यक ठरली"
                  + (f", त्यापैकी {devanagari_digits(str(g['n_queried']))} प्रकरणांत त्रुटी पत्राद्वारे मागवली" if g["n_queried"] else "") + ".")
    for v in g.get("validators", []):
        en.append(f"Check {v['code']}: {v['message']}.")
    if g.get("validators"):
        mr.append("कागदपत्रांच्या तपासणीत त्रुटी आढळली.")
    en.append(f"Basis: {b_en}.")
    mr.append(f"आधार: {b_mr}.")
    return " ".join(mr), " ".join(en)


def _llm_items(gaps: list[dict]) -> list[dict]:
    return [{"code": g["code"], "name_mr": g["name_mr"], "status": g["query_status"], "basis": g["basis"],
             "basis_mr": LABEL_TEXT[g["basis"]][1], "applicable_cases": g["n_applicable"], "supporting_cases": g["n_supporting"],
             "queried_cases": g["n_queried"], "failed_checks": [v["code"] for v in g.get("validators", [])]} for g in gaps]


def check(gaps: list[dict], out: dict, known_codes: set[str]) -> str | None:
    """None when the LLM output passes; else the reason it is rejected."""
    items = out.get("items") or []
    if [i["code"] for i in items] != [g["code"] for g in gaps]:
        return f"codes differ: expected {[g['code'] for g in gaps]}, got {[i['code'] for i in items]}"
    for g, it in zip(gaps, items):
        text = it["text_mr"]
        if not text.strip():
            return f"{g['code']}: empty text"
        allowed = {g["code"]} | {v["code"] for v in g.get("validators", [])}
        tokens = set(CODE_RE.findall(text)) | (set(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", text)) & known_codes)
        if tokens - allowed:
            return f"{g['code']}: mentions other codes {sorted(tokens - allowed)}"
        nums = set(NUM_RE.findall(ascii_digits(text)))
        own = {str(g["n_applicable"]), str(g["n_supporting"]), str(g["n_queried"])}
        if nums - own:
            return f"{g['code']}: numbers {sorted(nums - own)} are not among the gap's counts"
    return None


def explain(gaps: list[dict], *, llm: LLMClient | None, data_class: str, known_codes: set[str]) -> dict:
    """Fills explanation_mr / explanation_en on every gap; returns {"source", "check"}."""
    for g in gaps:
        g["explanation_mr"], g["explanation_en"] = template(g)
        g["explanation_source"] = "template"
    if not gaps or llm is None or not llm.available:
        return {"source": "template", "check": None if llm is None or not gaps else "no LLM configured"}
    try:
        out = llm.complete_json(task="gap_explanation", system=SYSTEM,
                                user=json.dumps({"items": _llm_items(gaps)}, ensure_ascii=False),
                                schema=SCHEMA, schema_name="gap_explanations", data_class=data_class)
    except LLMError as e:
        log.warning("gap explanation LLM failed: %s", e)
        return {"source": "template", "check": f"llm error: {e}"}
    problem = check(gaps, out, known_codes)
    if problem:
        log.warning("gap explanation rejected: %s", problem)
        return {"source": "template", "check": f"rejected: {problem}"}
    for g, it in zip(gaps, out["items"]):
        g["explanation_mr"], g["explanation_source"] = it["text_mr"].strip(), "llm"
    return {"source": "llm", "check": "passed"}
