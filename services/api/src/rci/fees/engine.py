"""Deterministic fee engine: premium (nazrana) for converting Class-II / Government-lease land to Class-I.

Pure Python `Decimal`, no LLM, no database. Every intermediate value is kept exact (50-digit context) and only
the total is rounded, so the trace can be re-derived by hand:

    market value   = area_sqm × asr_rate_per_sqm
    premium        = market value × rate_pct / 100
    clamped        = min(max(premium, min_amount), max_amount)
    adjustments    discount / rebate: − (amount or clamped × rate_pct / 100)
                   surcharge / penalty: + (amount or clamped × rate_pct / 100)
                   interest: + clamped × rate_pct / 100 × days / 365       (needs `days`)
    subtotal       = max(0, clamped ± adjustments)
    total          = round(subtotal, unit, mode)

Inputs come from `prepare.py` (card facts + rule slab + ASR row + parameters); this module only does arithmetic
and writes the trace.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import (
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
    localcontext,
)
from typing import Any

FEE_ENGINE_VERSION = "fee.1.0"
PAISE = Decimal("0.01")
DAYS_IN_YEAR = Decimal(365)
SUPPORTED_BASES = ("asr_land_value",)
ROUNDING_MODES = {"ROUND_HALF_UP": ROUND_HALF_UP, "ROUND_HALF_EVEN": ROUND_HALF_EVEN, "ROUND_UP": ROUND_UP,
                  "ROUND_DOWN": ROUND_DOWN, "ROUND_CEILING": ROUND_CEILING, "ROUND_FLOOR": ROUND_FLOOR}
MINUS_KINDS = ("discount", "rebate")
PLUS_KINDS = ("surcharge", "penalty")


class FeeInputError(ValueError):
    """Inputs the engine cannot compute with (non-positive area, unsupported rate basis, ...)."""


def money(x: Decimal) -> str:
    return str(x.quantize(PAISE, rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class Rounding:
    mode: str = "ROUND_HALF_UP"
    unit: Decimal = Decimal("1")

    def __post_init__(self):
        if self.mode not in ROUNDING_MODES:
            raise FeeInputError(f"unknown rounding mode {self.mode!r}")
        if self.unit <= 0:
            raise FeeInputError("rounding unit must be positive")

    def apply(self, x: Decimal) -> Decimal:
        steps = (x / self.unit).quantize(Decimal(1), rounding=ROUNDING_MODES[self.mode])
        return (steps * self.unit).quantize(PAISE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Adjustment:
    code: str
    kind: str  # discount | rebate | surcharge | penalty | interest
    rate_pct: Decimal | None = None
    amount: Decimal | None = None
    days: int | None = None  # interest only
    status: str = "to_verify"
    rule_set_code: str | None = None
    source_ref: str | None = None


@dataclass(frozen=True)
class FeeInputs:
    area_sqm: Decimal
    asr_rate_per_sqm: Decimal
    rate_pct: Decimal
    rate_basis: str = "asr_land_value"
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    adjustments: tuple[Adjustment, ...] = ()
    rounding: Rounding = Rounding()


@dataclass
class TraceStep:
    step: str
    label: str
    value: str | None = None
    formula: str | None = None
    inputs: dict = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)  # evidence / rule / ASR references
    status: str = "ok"  # ok | unverified | assumption | blocked | skipped
    note: str | None = None

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Computation:
    total: Decimal
    subtotal: Decimal  # exact, before rounding
    components: dict[str, str]
    trace: list[TraceStep]


def compute(inp: FeeInputs) -> Computation:
    """The arithmetic. Raises FeeInputError on inputs it cannot use; never guesses."""
    if inp.rate_basis not in SUPPORTED_BASES:
        raise FeeInputError(f"unsupported rate basis {inp.rate_basis!r} (supported: {', '.join(SUPPORTED_BASES)})")
    if inp.area_sqm <= 0:
        raise FeeInputError("area must be positive")
    if inp.asr_rate_per_sqm <= 0:
        raise FeeInputError("ASR rate must be positive")
    if inp.rate_pct < 0:
        raise FeeInputError("premium rate cannot be negative")
    if inp.min_amount is not None and inp.max_amount is not None and inp.min_amount > inp.max_amount:
        raise FeeInputError("slab minimum is above its maximum")
    trace: list[TraceStep] = []
    with localcontext() as ctx:
        ctx.prec = 50
        value = inp.area_sqm * inp.asr_rate_per_sqm
        trace.append(TraceStep("calc.market_value", "Market value of the land", money(value),
                               f"{inp.area_sqm} sq m × ₹{inp.asr_rate_per_sqm}/sq m",
                               {"area_sqm": str(inp.area_sqm), "asr_rate_per_sqm": str(inp.asr_rate_per_sqm)}))
        premium = value * inp.rate_pct / 100
        trace.append(TraceStep("calc.premium", "Premium at the slab rate", money(premium),
                               f"₹{money(value)} × {inp.rate_pct}%", {"rate_pct": str(inp.rate_pct)}))
        clamped = premium
        if inp.min_amount is not None and clamped < inp.min_amount:
            clamped = inp.min_amount
        if inp.max_amount is not None and clamped > inp.max_amount:
            clamped = inp.max_amount
        if inp.min_amount is not None or inp.max_amount is not None:
            trace.append(TraceStep("calc.clamp", "Slab minimum / maximum", money(clamped),
                                   f"min({inp.max_amount if inp.max_amount is not None else '∞'}, "
                                   f"max({inp.min_amount if inp.min_amount is not None else 0}, ₹{money(premium)}))",
                                   {"min_amount": str(inp.min_amount) if inp.min_amount is not None else None,
                                    "max_amount": str(inp.max_amount) if inp.max_amount is not None else None},
                                   note=None if clamped == premium else "premium clamped to the slab limit"))
        running = clamped
        components = {"market_value": money(value), "premium": money(premium), "premium_after_limits": money(clamped)}
        order = [a for a in inp.adjustments if a.kind in MINUS_KINDS] + [a for a in inp.adjustments if a.kind in PLUS_KINDS] \
            + [a for a in inp.adjustments if a.kind == "interest"]
        unknown = [a.code for a in inp.adjustments if a.kind not in MINUS_KINDS + PLUS_KINDS + ("interest",)]
        if unknown:
            raise FeeInputError(f"unknown adjustment kind for {unknown}")
        for a in order:
            src = [{"kind": "rule", "rule_set_code": a.rule_set_code, "code": a.code, "status": a.status, "source_ref": a.source_ref}]
            status = "ok" if a.status == "verified" else "unverified"
            if a.kind == "interest":
                if a.rate_pct is None or a.days is None:
                    trace.append(TraceStep(f"adj.{a.code}", f"Interest {a.code}", None, None, {}, src, "skipped",
                                           "needs an interest rate and the number of days late"))
                    continue
                delta = clamped * a.rate_pct / 100 * Decimal(a.days) / DAYS_IN_YEAR
                formula = f"₹{money(clamped)} × {a.rate_pct}% × {a.days}/365"
            elif a.amount is not None:
                delta, formula = a.amount, f"fixed ₹{a.amount}"
            elif a.rate_pct is not None:
                delta, formula = clamped * a.rate_pct / 100, f"₹{money(clamped)} × {a.rate_pct}%"
            else:
                trace.append(TraceStep(f"adj.{a.code}", f"{a.kind.capitalize()} {a.code}", None, None, {}, src, "skipped",
                                       "rate / amount not filled in"))
                continue
            signed = -delta if a.kind in MINUS_KINDS else delta
            running += signed
            components[f"adj.{a.code}"] = money(signed)
            trace.append(TraceStep(f"adj.{a.code}", f"{a.kind.capitalize()} {a.code}", money(signed),
                                   ("− " if signed < 0 else "+ ") + formula, {"kind": a.kind}, src, status))
        subtotal = max(running, Decimal(0))
        if subtotal != running:
            trace.append(TraceStep("calc.floor", "Adjustments cannot make the premium negative", money(subtotal), "max(0, …)"))
        total = inp.rounding.apply(subtotal)
        trace.append(TraceStep("calc.rounding", "Rounding", money(total),
                               f"{inp.rounding.mode} to ₹{inp.rounding.unit}", {"exact": str(subtotal)}))
        components["total"] = money(total)
    return Computation(total=total, subtotal=subtotal, components=components, trace=trace)


def to_decimal(v: Any) -> Decimal | None:
    """Card / DB values → Decimal (strings keep their exact digits; floats go through str)."""
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):
        raise FeeInputError("boolean is not a number")
    return Decimal(str(v).replace(",", ""))
