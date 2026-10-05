"""Numeric / date / ID cross-check of draft text against the structured facts.

Every number, date and identifier in a narrative slot (and in every rendered clause) must be traceable to the case:
  * dates     dd/mm/yyyy, dd.mm.yyyy, dd-mm-yyyy, ISO and "२८ जून २०२४", in either digit script → must be a date on the
              card (a fact, an exhibit date or an event date)
  * IDs       CTS no., GRN, order / registration / reference numbers, head of account → removed first when they are
              the case's own (so their digits are not read as loose numbers)
  * numbers   everything else (Indian grouping, "/-" and paise allowed) → must equal (as a decimal) a fact value, a fee
              figure or one of the counts the draft states (queries, items, reports)
The allowed set is built from the card, the fee result and the drafting slots — never from free text.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from rci.drafting.mrtext import MONTHS
from rci.pipeline.normalise import ascii_digits

DATE_DMY = re.compile(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})(?!\d)")
DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
DATE_LONG = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})(?!\d)")
NUMBER = re.compile(r"(?<!\w)\d[\d,]*(?:\.\d+)?")
NAME_PHRASES = re.compile(r"(?:वर्ग|कक्ष)-\d")
ISO_VALUE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Allowed:
    dates: set[dt.date] = field(default_factory=set)
    numbers: set[Decimal] = field(default_factory=set)
    ids: set[str] = field(default_factory=set)

    def add_value(self, v) -> None:
        if v is None or isinstance(v, bool) or v == "":
            return
        if isinstance(v, (dt.date, dt.datetime)):
            self.dates.add(v if isinstance(v, dt.date) and not isinstance(v, dt.datetime) else v.date())
            return
        s = ascii_digits(str(v)).strip()
        if ISO_VALUE.match(s[:10]) and (len(s) == 10 or s[10] in "T "):
            try:
                self.dates.add(dt.date.fromisoformat(s[:10]))
                return
            except ValueError:
                pass
        n = _dec(s)
        if n is not None:
            self.numbers.add(n)
        elif re.search(r"\d", s):
            self.ids.add(s)

    def add_all(self, obj) -> None:
        """Walk a card / fee JSON and collect every value."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("snippet", "raw", "alternatives", "message", "formula", "note", "document", "page_id", "exhibit_id",
                         "case_id", "id", "card_id"):
                    continue
                self.add_all(v)
        elif isinstance(obj, list):
            for x in obj:
                self.add_all(x)
        else:
            self.add_value(obj)


def _dec(s: str) -> Decimal | None:
    t = s.replace(",", "").rstrip("/-").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", t):
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def allowed_from(card: dict, *, fee: dict | None = None, extra: list | None = None) -> Allowed:
    a = Allowed()
    a.add_all(card.get("facts") or {})
    for e in card.get("exhibits") or []:
        a.add_value(e.get("doc_date"))
    for e in card.get("events") or []:
        a.add_value(e.get("date"))
    if fee:
        a.add_all({k: fee.get(k) for k in ("total", "components", "inputs")})
    for x in extra or []:
        a.add_value(x)
    return a


def check(text: str, allowed: Allowed) -> list[dict]:
    """Violations: [{kind: date|number, value, context}] (empty = every figure is traceable)."""
    t = ascii_digits(text)
    for ident in sorted(allowed.ids, key=len, reverse=True):
        t = t.replace(ident, " ")
    t = NAME_PHRASES.sub(" ", t)
    out = []

    def ctx(m) -> str:
        return t[max(0, m.start() - 20): m.end() + 20].strip()

    def take_dates(pattern, to_date):
        nonlocal t
        for m in list(pattern.finditer(t)):
            try:
                d = to_date(m)
            except ValueError:
                d = None
            if d is None or d not in allowed.dates:
                out.append({"kind": "date", "value": m.group(0), "context": ctx(m)})
        t = pattern.sub(" ", t)

    take_dates(DATE_LONG, lambda m: dt.date(int(m.group(3)), MONTHS.index(m.group(2)) + 1, int(m.group(1))))
    take_dates(DATE_ISO, lambda m: dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    take_dates(DATE_DMY, lambda m: dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
    for m in NUMBER.finditer(t):
        n = _dec(m.group(0))
        if n is None or n not in allowed.numbers:
            out.append({"kind": "number", "value": m.group(0), "context": ctx(m)})
    return out
