"""Topics, document types, folder hints (config/library/topics.yaml) and the search glossary (glossary.yaml)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from rci.config import get_settings


def _dir() -> Path:
    return get_settings().repo_root / "config" / "library"


@dataclass(frozen=True)
class Taxonomy:
    topics: dict[str, dict]
    types: dict[str, dict]
    folders: dict[str, list[str]]
    case_folders: dict[str, str]
    skip: list[str]
    starter_hints: list[str]


@lru_cache
def taxonomy() -> Taxonomy:
    raw = yaml.safe_load((_dir() / "topics.yaml").read_text(encoding="utf-8")) or {}
    return Taxonomy(raw.get("topics") or {}, raw.get("types") or {}, raw.get("folders") or {}, raw.get("case_folders") or {},
                    list(raw.get("skip") or []), list(raw.get("starter_hints") or []))


@lru_cache
def glossary() -> list[tuple[re.Pattern, list[str]]]:
    raw = yaml.safe_load((_dir() / "glossary.yaml").read_text(encoding="utf-8")) or {}
    out = []
    for t in raw.get("terms") or []:
        alts = sorted((e.strip() for e in t["en"]), key=len, reverse=True)
        pat = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(a).replace(r"\ ", r"[\s-]+") for a in alts) + r")(?![\w-])", re.I)
        out.append((pat, list(t["mr"])))
    return out


def glossary_terms(query: str) -> list[str]:
    """Marathi terms for the English phrases in the query (deterministic)."""
    terms: list[str] = []
    for pat, mr in glossary():
        if pat.search(query):
            terms += [m for m in mr if m not in terms]
    return terms


TYPE_SHORT = {"gr": "GR", "gazette": "Notification", "circular": "Circular", "letter": "Letter", "case_note": "Case note",
              "court_order": "Court order", "other": "Document"}
