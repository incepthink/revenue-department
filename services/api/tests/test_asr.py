"""ASR CSV import: header checks, row validation, duplicates, dry run vs commit, replace."""
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from conftest import SAMPLES
from rci.asr import import_asr, parse_csv, validate

HEADER = "year,district,taluka,village,zone,land_rate_per_sqm,residential_rate_per_sqm,source_ref,status\n"


def _csv(*rows: str) -> bytes:
    return (HEADER + "\n".join(rows) + "\n").encode("utf-8")


def test_header_errors():
    rows, issues = parse_csv(b"year,village,land_rate\n2024,x,1\n")
    assert not rows
    assert {(i.column, i.message) for i in issues} == {
        ("district", "required column missing"), ("land_rate_per_sqm", "required column missing"), ("land_rate", "unknown column")}


def test_non_utf8_rejected():
    _, issues = parse_csv("year,district,village,land_rate_per_sqm\n2024,पुणे,x,1\n".encode("utf-16"))
    assert issues and "UTF-8" in issues[0].message


def test_row_validation():
    rows, _ = parse_csv(_csv(
        "२०२४,पुणे,हवेली,कोथरूड,12,\"48,500\",98200,,to_verify",   # Devanagari year + Indian grouping → ok
        "1890,पुणे,हवेली,बाणेर,22,100,,,",                          # bad year
        "2024,,हवेली,बाणेर,22,100,,,",                              # district missing
        "2024,पुणे,हवेली,औंध,5,abc,,,",                              # not a number
        "2024,पुणे,हवेली,पाषाण,5,-3,,,",                             # negative
        "2024,पुणे,हवेली,सुस,5,10.123,,,",                           # 3 decimals
        "2024,पुणे,हवेली,बावधन,5,100,,,verified",                    # verified without source
        "2024,पुणे,हवेली,कोथरूड,12,48500,98200,,maybe",              # bad status + duplicate of row 1
    ))
    clean, errors, _ = validate(rows)
    got = {(e.row, e.column) for e in errors}
    assert clean[0]["year"] == 2024 and clean[0]["land_rate_per_sqm"] == Decimal("48500")
    assert {(2, "year"), (3, "district"), (4, "land_rate_per_sqm"), (5, "land_rate_per_sqm"), (6, "land_rate_per_sqm"),
            (7, "source_ref"), (8, "status"), (8, None)} <= got
    assert not any(e.row == 1 for e in errors)


def test_warnings_do_not_block():
    rows, _ = parse_csv(_csv("2024,पुणे,हवेली,कोथरूड,12,50000,40000,,"))
    _, errors, warnings = validate(rows)
    assert not errors and warnings[0].column == "residential_rate_per_sqm"


@pytest.mark.db
def test_import_dry_run_commit_and_replace(db_session):
    from rci.db.models import AsrRate

    data = (SAMPLES / "asr" / "asr_synthetic_pune.csv").read_bytes()
    count = lambda: db_session.scalar(select(func.count()).select_from(AsrRate))  # noqa: E731
    before = count()
    dry = import_asr(db_session, data, dry_run=True)
    assert dry.ok and dry.would_insert == 6 and count() == before
    done = import_asr(db_session, data, dry_run=False)
    assert done.ok and done.inserted == 6 and count() == before + 6 and done.batch_id
    again = import_asr(db_session, data, dry_run=False)
    assert not again.ok and len(again.errors) == 6 and count() == before + 6  # all-or-nothing
    replaced = import_asr(db_session, data, dry_run=False, replace=True)
    assert replaced.ok and replaced.replaced == 6 and count() == before + 6
    row = db_session.scalar(select(AsrRate).where(AsrRate.village == "कोथरूड", AsrRate.year == 2025))
    assert row.land_rate_per_sqm == Decimal("50900.00") and row.status == "to_verify"
    for r in db_session.scalars(select(AsrRate).where(AsrRate.import_batch_id == replaced.batch_id)):
        db_session.delete(r)
    db_session.flush()
