"""Rule resolution: which rule sets are in force on a date, and which premium slab applies.

    stack = stack_for(session, on=date)            # DB → RuleStack (pure data from here on)
    res = resolve_slab(stack, applicant_category="CHS", holding_basis="govt_lease", land_use="residential",
                       facts={"tenure": "govt_lease"})

Stacking
  A rule set is in force on `on` when effective_from <= on <= effective_to (open end = in force). A rule set
  that another in-force rule set `supersedes` drops out. The remaining sets are ordered by precedence
  (base < amendment / circular < amnesty), then notification date. The highest-precedence set with a
  matching slab wins; lower sets are the fallback (e.g. once an amnesty window closes, the amended base
  rate applies again).

Failure modes (never silently pick a number):
  UnverifiedRule  the chosen slab or its rule set is `to_verify`, or its rate is not filled in yet.
                  The exception carries the resolution so reviewers can see what *would* apply.
  AmbiguousRule   two slabs match at the same precedence with different terms, or a slab condition needs
                  a fact that was not supplied.
  NoRuleFound     nothing matches.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

KIND_PRECEDENCE = {"base": 0, "amendment": 1, "circular": 1, "amnesty": 2}


class RuleError(Exception):
    pass


class NoRuleFound(RuleError, LookupError):
    pass


class AmbiguousRule(RuleError):
    def __init__(self, message: str, candidates: list["SlabRow"] | None = None, needs: list[str] | None = None):
        super().__init__(message)
        self.candidates = candidates or []
        self.needs = needs or []


class UnverifiedRule(RuleError):
    def __init__(self, message: str, resolution: "SlabResolution"):
        super().__init__(message)
        self.resolution = resolution


@dataclass(frozen=True)
class RuleSetRow:
    code: str
    kind: str
    effective_from: dt.date
    effective_to: dt.date | None = None
    notification_date: dt.date | None = None
    supersedes: str | None = None  # code of the superseded rule set
    status: str = "to_verify"
    name: str = ""
    id: Any = None

    @property
    def precedence(self) -> int:
        return KIND_PRECEDENCE.get(self.kind, 1)

    def in_force(self, on: dt.date) -> bool:
        return self.effective_from <= on and (self.effective_to is None or on <= self.effective_to)


@dataclass(frozen=True)
class SlabRow:
    rule_set_code: str
    applicant_category: str
    holding_basis: str
    land_use: str
    rate_pct: Decimal | None
    rate_basis: str
    window_from: dt.date | None = None
    window_to: dt.date | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    conditions: dict = field(default_factory=dict)
    status: str = "to_verify"
    source_ref: str | None = None
    seed_key: str | None = None
    id: Any = None

    def in_window(self, on: dt.date) -> bool:
        return (self.window_from is None or self.window_from <= on) and (self.window_to is None or on <= self.window_to)

    def terms(self) -> tuple:
        return (self.rate_pct, self.rate_basis, self.min_amount, self.max_amount)

    def to_json(self) -> dict:
        return {
            "id": str(self.id) if self.id else None, "rule_set_code": self.rule_set_code, "seed_key": self.seed_key,
            "applicant_category": self.applicant_category, "holding_basis": self.holding_basis, "land_use": self.land_use,
            "rate_pct": str(self.rate_pct) if self.rate_pct is not None else None, "rate_basis": self.rate_basis,
            "window_from": self.window_from.isoformat() if self.window_from else None,
            "window_to": self.window_to.isoformat() if self.window_to else None,
            "min_amount": str(self.min_amount) if self.min_amount is not None else None,
            "max_amount": str(self.max_amount) if self.max_amount is not None else None,
            "conditions": self.conditions, "status": self.status, "source_ref": self.source_ref,
        }


@dataclass(frozen=True)
class CodedRow:
    """rule_requirements / rule_obligations / rule_adjustments / rule_document_checks, reduced to what stacking needs."""

    rule_set_code: str
    code: str
    condition: dict = field(default_factory=dict)
    status: str = "to_verify"
    data: dict = field(default_factory=dict)  # table-specific columns (mandatory, rate_pct, applies_from, ...)


@dataclass
class RuleStack:
    on: dt.date
    rule_sets: list[RuleSetRow]  # lowest → highest precedence
    slabs: list[SlabRow] = field(default_factory=list)
    requirements: list[CodedRow] = field(default_factory=list)
    obligations: list[CodedRow] = field(default_factory=list)
    adjustments: list[CodedRow] = field(default_factory=list)
    document_checks: list[CodedRow] = field(default_factory=list)

    @property
    def codes(self) -> list[str]:
        return [r.code for r in self.rule_sets]

    def rule_set(self, code: str) -> RuleSetRow:
        return next(r for r in self.rule_sets if r.code == code)

    def to_json(self) -> dict:
        return {"on": self.on.isoformat(), "rule_sets": [
            {"code": r.code, "kind": r.kind, "status": r.status, "effective_from": r.effective_from.isoformat(),
             "effective_to": r.effective_to.isoformat() if r.effective_to else None} for r in self.rule_sets]}


@dataclass
class SlabResolution:
    slab: SlabRow
    rule_set: RuleSetRow
    stack: list[str]
    on: dt.date
    trace: list[str]

    @property
    def verified(self) -> bool:
        return self.slab.status == "verified" and self.rule_set.status == "verified" and self.slab.rate_pct is not None

    def to_json(self) -> dict:
        return {"slab": self.slab.to_json(), "rule_set": self.rule_set.code, "rule_set_status": self.rule_set.status,
                "stack": self.stack, "on": self.on.isoformat(), "verified": self.verified, "trace": self.trace}


# ------------------------------------------------------------------------------------------------
# Stacking
# ------------------------------------------------------------------------------------------------
def select_stack(rule_sets: list[RuleSetRow], on: dt.date) -> list[RuleSetRow]:
    in_force = [r for r in rule_sets if r.in_force(on)]
    superseded = {r.supersedes for r in in_force if r.supersedes}
    active = [r for r in in_force if r.code not in superseded]
    return sorted(active, key=lambda r: (r.precedence, r.notification_date or r.effective_from, r.effective_from, r.code))


def build_stack(on: dt.date, rule_sets: list[RuleSetRow], slabs: list[SlabRow] = (), requirements: list[CodedRow] = (),
                obligations: list[CodedRow] = (), adjustments: list[CodedRow] = (),
                document_checks: list[CodedRow] = ()) -> RuleStack:
    active = select_stack(list(rule_sets), on)
    codes = {r.code for r in active}
    return RuleStack(
        on=on, rule_sets=active,
        slabs=[s for s in slabs if s.rule_set_code in codes],
        requirements=[r for r in requirements if r.rule_set_code in codes],
        obligations=[r for r in obligations if r.rule_set_code in codes],
        adjustments=[r for r in adjustments if r.rule_set_code in codes],
        document_checks=[r for r in document_checks if r.rule_set_code in codes],
    )


# ------------------------------------------------------------------------------------------------
# Conditions: {"fact": value} or {"fact": {"op": value}}; ops eq ne in not_in lt lte gt gte.
# ------------------------------------------------------------------------------------------------
_OPS = {
    "eq": lambda a, b: a == b, "ne": lambda a, b: a != b, "in": lambda a, b: a in b, "not_in": lambda a, b: a not in b,
    "lt": lambda a, b: a < b, "lte": lambda a, b: a <= b, "gt": lambda a, b: a > b, "gte": lambda a, b: a >= b,
}


def _coerce(actual: Any, expected: Any) -> Any:
    if isinstance(actual, dt.date) and isinstance(expected, str):
        return dt.date.fromisoformat(expected)
    if isinstance(actual, (int, float, Decimal)) and isinstance(expected, (int, float, str)) and not isinstance(expected, bool):
        return Decimal(str(expected))
    return expected


def evaluate(condition: dict, facts: dict) -> tuple[bool | None, list[str]]:
    """True / False, or None with the list of missing fact keys when it cannot be decided."""
    missing: list[str] = []
    for key, spec in (condition or {}).items():
        if key not in facts or facts[key] is None:
            missing.append(key)
            continue
        actual = facts[key]
        if isinstance(actual, (int, float)) and not isinstance(actual, bool):
            actual = Decimal(str(actual))
        ops = spec if isinstance(spec, dict) else {"eq": spec}
        for op, expected in ops.items():
            if op not in _OPS:
                raise ValueError(f"unknown condition operator {op!r}")
            exp = [_coerce(actual, e) for e in expected] if op in ("in", "not_in") else _coerce(actual, expected)
            if not _OPS[op](actual, exp):
                return False, []
    return (None, missing) if missing else (True, [])


# ------------------------------------------------------------------------------------------------
# Slab resolution
# ------------------------------------------------------------------------------------------------
def resolve_slab(stack: RuleStack, *, applicant_category: str, holding_basis: str, land_use: str,
                 facts: dict | None = None, on: dt.date | None = None, allow_unverified: bool = False) -> SlabResolution:
    on = on or stack.on
    facts = facts or {}
    trace = [f"stack on {stack.on.isoformat()}: {' < '.join(stack.codes) or '(empty)'}"]
    key = (applicant_category, holding_basis, land_use)
    by_level: dict[int, list[tuple[RuleSetRow, list[SlabRow]]]] = {}
    for rs in stack.rule_sets:
        by_level.setdefault(rs.precedence, []).append((rs, [s for s in stack.slabs if s.rule_set_code == rs.code]))

    for level in sorted(by_level, reverse=True):
        matched: list[tuple[RuleSetRow, SlabRow]] = []
        needs: list[str] = []
        for rs, slabs in by_level[level]:
            for s in slabs:
                if (s.applicant_category, s.holding_basis, s.land_use) != key:
                    continue
                if not s.in_window(on):
                    trace.append(f"{rs.code}: slab {s.seed_key or s.id} window {s.window_from}..{s.window_to} excludes {on}")
                    continue
                ok, missing = evaluate(s.conditions, facts)
                if ok is None:
                    needs += missing
                elif ok:
                    matched.append((rs, s))
        if needs and not matched:
            raise AmbiguousRule(f"slab conditions need facts: {sorted(set(needs))}", needs=sorted(set(needs)))
        if not matched:
            continue
        if len({s.terms() for _, s in matched}) > 1:
            raise AmbiguousRule(
                f"{len(matched)} slabs match {key} on {on} with different terms: "
                + ", ".join(f"{rs.code}:{s.seed_key or s.id}" for rs, s in matched),
                candidates=[s for _, s in matched],
            )
        rs, slab = matched[-1]  # identical terms: the latest notification is the citation
        rate = f"{slab.rate_pct}% of {slab.rate_basis}" if slab.rate_pct is not None else "rate not filled in"
        trace.append(f"selected {rs.code} slab {slab.seed_key or slab.id} ({rate})")
        res = SlabResolution(slab=slab, rule_set=rs, stack=stack.codes, on=on, trace=trace)
        if not res.verified and not allow_unverified:
            why = []
            if rs.status != "verified":
                why.append(f"rule set {rs.code} is to_verify")
            if slab.status != "verified":
                why.append("slab is to_verify")
            if slab.rate_pct is None:
                why.append("rate not filled in")
            raise UnverifiedRule(f"{key} on {on}: " + "; ".join(why), res)
        return res
    raise NoRuleFound(f"no premium slab for {key} on {on} (stack: {stack.codes})")


def _merge_coded(rows: list[CodedRow], stack: RuleStack, facts: dict) -> list[dict]:
    """Later (higher-precedence) rule sets override earlier rows with the same code."""
    order = {c: i for i, c in enumerate(stack.codes)}
    out: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: order[r.rule_set_code]):
        applies, needs = evaluate(r.condition, facts)
        out[r.code] = {"code": r.code, "rule_set_code": r.rule_set_code, "status": r.status,
                       "applies": applies, "needs": needs, **r.data}
    return list(out.values())


def requirements_for(stack: RuleStack, facts: dict | None = None) -> list[dict]:
    return _merge_coded(stack.requirements, stack, facts or {})


def obligations_for(stack: RuleStack, facts: dict | None = None) -> list[dict]:
    return _merge_coded(stack.obligations, stack, facts or {})


def adjustments_for(stack: RuleStack, facts: dict | None = None) -> list[dict]:
    rows = [r for r in stack.adjustments
            if (r.data.get("applies_from") is None or r.data["applies_from"] <= stack.on)
            and (r.data.get("applies_to") is None or stack.on <= r.data["applies_to"])]
    return _merge_coded(rows, stack, facts or {})


def document_checks_for(stack: RuleStack, slot: str | None = None, facts: dict | None = None) -> list[dict]:
    """What a document in `slot` must show (all slots when None). Stacked like requirements: a later rule set's row with
    the same code replaces the earlier one, slot included."""
    rows = _merge_coded(stack.document_checks, stack, facts or {})
    return [r for r in rows if slot is None or r["slot"] == slot]
