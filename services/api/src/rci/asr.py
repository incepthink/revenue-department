"""ASR (Annual Statement of Rates / रेडी रेकनर) CSV import with validation.

Columns (header row, any order; UTF-8, optional BOM):
  required  year, district, village, land_rate_per_sqm
  optional  division, taluka, zone, sub_zone, cts_range, residential_rate_per_sqm, commercial_rate_per_sqm,
            source_ref, status (verified | to_verify, default to_verify)

The import is all-or-nothing: any error rejects the whole file. `dry_run` validates and reports what would
change. A row whose natural key (year, district, taluka, village, zone, sub_zone, cts_range) already exists
is an error unless `replace=True`, in which case the old row is replaced.
"""
from __future__ import annotations

import csv
import io
import re
import statistics
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from rci.pipeline.normalise import ascii_digits

REQUIRED = ("year", "district", "village", "land_rate_per_sqm")
OPTIONAL = ("division", "taluka", "zone", "sub_zone", "cts_range", "residential_rate_per_sqm", "commercial_rate_per_sqm",
            "source_ref", "status")
RATES = ("land_rate_per_sqm", "residential_rate_per_sqm", "commercial_rate_per_sqm")
KEY = ("year", "district", "taluka", "village", "zone", "sub_zone", "cts_range")
MAX_ROWS = 50_000


@dataclass
class Issue:
    row: int  # 1-based data row (header = row 0)
    column: str | None
    message: str


@dataclass
class ImportReport:
    ok: bool
    dry_run: bool
    rows: int
    would_insert: int = 0
    would_replace: int = 0
    inserted: int = 0
    replaced: int = 0
    batch_id: str | None = None
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    def to_json(self) -> dict:
        d = asdict(self)
        d["errors"] = d["errors"][:200]
        d["warnings"] = d["warnings"][:200]
        d["error_count"], d["warning_count"] = len(self.errors), len(self.warnings)
        return d


def _text(v: str | None) -> str | None:
    v = unicodedata.normalize("NFC", (v or "")).strip()
    return re.sub(r"\s+", " ", v) or None


def _rate(v: str | None) -> Decimal | None:
    s = ascii_digits(_text(v) or "").replace(",", "").replace("₹", "").strip()
    if not s:
        return None
    d = Decimal(s)  # raises InvalidOperation
    if not d.is_finite():
        raise InvalidOperation
    return d


def parse_csv(data: bytes) -> tuple[list[dict], list[Issue]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [Issue(0, None, "file is not UTF-8 (save the CSV as UTF-8)")]
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    issues = [Issue(0, c, "required column missing") for c in REQUIRED if c not in header]
    issues += [Issue(0, c, "unknown column") for c in header if c and c not in REQUIRED + OPTIONAL]
    if issues:
        return [], issues
    reader.fieldnames = header
    rows = list(reader)
    if len(rows) > MAX_ROWS:
        return [], [Issue(0, None, f"too many rows ({len(rows)} > {MAX_ROWS})")]
    return rows, []


def validate(rows: list[dict]) -> tuple[list[dict], list[Issue], list[Issue]]:
    clean: list[dict] = []
    errors: list[Issue] = []
    warnings: list[Issue] = []
    seen: dict[tuple, int] = {}
    for i, raw in enumerate(rows, start=1):
        if None in raw:  # more cells than headers
            errors.append(Issue(i, None, "row has more cells than the header"))
            continue
        r = {k: _text(raw.get(k)) for k in REQUIRED + OPTIONAL}
        out: dict = {k: r[k] for k in ("division", "district", "taluka", "village", "zone", "sub_zone", "cts_range", "source_ref")}
        try:
            year = int(ascii_digits(r["year"] or ""))
            if not 1990 <= year <= 2100:
                raise ValueError
            out["year"] = year
        except ValueError:
            errors.append(Issue(i, "year", f"not a year: {r['year']!r}"))
        for c in ("district", "village"):
            if not r[c]:
                errors.append(Issue(i, c, "required"))
        for c in RATES:
            try:
                v = _rate(r[c])
            except InvalidOperation:
                errors.append(Issue(i, c, f"not a number: {r[c]!r}"))
                continue
            if v is None:
                if c == "land_rate_per_sqm":
                    errors.append(Issue(i, c, "required"))
                out[c] = None
                continue
            if v <= 0:
                errors.append(Issue(i, c, "must be > 0"))
            elif v.as_tuple().exponent < -2:
                errors.append(Issue(i, c, "more than 2 decimal places"))
            out[c] = v
        status = (r["status"] or "to_verify").lower()
        if status not in ("verified", "to_verify"):
            errors.append(Issue(i, "status", f"must be verified or to_verify, got {r['status']!r}"))
        elif status == "verified" and not out["source_ref"]:
            errors.append(Issue(i, "source_ref", "a verified row needs source_ref (gazette / ASR book page)"))
        out["status"] = status
        if out.get("residential_rate_per_sqm") and out.get("land_rate_per_sqm") and out["residential_rate_per_sqm"] < out["land_rate_per_sqm"]:
            warnings.append(Issue(i, "residential_rate_per_sqm", "residential rate is below the open-land rate"))
        if "year" in out and out["district"] and out["village"]:
            key = tuple(out.get(k) for k in KEY)
            if key in seen:
                errors.append(Issue(i, None, f"duplicate of row {seen[key]} (same year/district/taluka/village/zone/sub_zone/cts_range)"))
            else:
                seen[key] = i
        clean.append(out)
    land = [r["land_rate_per_sqm"] for r in clean if r.get("land_rate_per_sqm")]
    if len(land) >= 5:
        med = statistics.median(land)
        for i, r in enumerate(clean, start=1):
            v = r.get("land_rate_per_sqm")
            if v and (v > med * 10 or v < med / 10):
                warnings.append(Issue(i, "land_rate_per_sqm", f"{v} is more than 10× away from the file median {med}"))
    years = [r["year"] for r in clean if "year" in r]
    if years and len(set(years)) > 1:
        common = statistics.mode(years)
        for i, r in enumerate(clean, start=1):
            if r.get("year") not in (None, common):
                warnings.append(Issue(i, "year", f"year {r['year']} differs from most rows ({common})"))
    return clean, errors, warnings


def _key_filter(model, row: dict):
    conds = []
    for k in KEY:
        col = getattr(model, k)
        conds.append(col.is_(None) if row.get(k) is None else col == row[k])
    return conds


def import_asr(session: Session, data: bytes, *, dry_run: bool = True, replace: bool = False,
               imported_by: uuid.UUID | None = None) -> ImportReport:
    from rci.db.models import AsrRate

    rows, header_issues = parse_csv(data)
    if header_issues:
        return ImportReport(ok=False, dry_run=dry_run, rows=0, errors=header_issues)
    clean, errors, warnings = validate(rows)
    report = ImportReport(ok=False, dry_run=dry_run, rows=len(rows), errors=errors, warnings=warnings)
    existing: list[tuple[dict, list]] = []
    for i, r in enumerate(clean, start=1):
        if "year" not in r:
            continue
        found = session.scalars(select(AsrRate).where(*_key_filter(AsrRate, r))).all()
        if found and not replace:
            errors.append(Issue(i, None, f"already imported ({len(found)} row(s) with the same key); use replace"))
        existing.append((r, list(found)))
    report.would_replace = sum(1 for _, f in existing if f)
    report.would_insert = len(clean) - report.would_replace
    if not clean:
        errors.append(Issue(0, None, "no data rows"))
    report.ok = not errors
    if dry_run or not report.ok:
        return report
    batch = uuid.uuid4()
    for r, found in existing:
        for old in found:
            session.delete(old)
        session.add(AsrRate(**{k: r.get(k) for k in ("year", "division", "district", "taluka", "village", "zone", "sub_zone",
                                                      "cts_range", "source_ref", "status") + RATES},
                            import_batch_id=batch, imported_by=imported_by))
    session.flush()
    report.batch_id, report.inserted, report.replaced = str(batch), report.would_insert, report.would_replace
    return report
