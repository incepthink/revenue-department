"""Reproducible backtest gate: fresh database → seed → synthetic pool → holdouts → clause mining → backtest → gate.

    python eval/backtest/ci_backtest.py                      # gate against eval/backtest/baseline.json (exit 1 on regression)
    python eval/backtest/ci_backtest.py --update-baseline    # write the baseline from this run (commit it deliberately)
    python eval/backtest/ci_backtest.py --database-url postgresql+psycopg://rci:rci@localhost:5432/rci_ci   # CI

Everything is deterministic (seeded pool, seeded stratified selection, local hash embeddings, template explanations and
narratives, the committed config/intel_tuned.yaml), so CI and a developer machine produce the same numbers. The dev
database is never touched: the script (re)creates its own database (default rci_ci next to the configured one).
The synthetic pool is invented data — this gate proves the machinery (masking, time travel, metrics, drafts, leak
checks) and catches regressions; the department's 20 real holdouts are measured with `rci backtest --data-class real`.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POOL, HOLDOUTS, SEED, PREFIX = 60, 20, 7, "SYN-P"


def recreate(url: str) -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    u = make_url(url)
    admin = create_engine(u.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{u.database}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{u.database}"'))
    admin.dispose()
    with create_engine(u).begin() as c:
        for stmt in ("CREATE EXTENSION IF NOT EXISTS vector", "CREATE EXTENSION IF NOT EXISTS pgcrypto", "CREATE SCHEMA IF NOT EXISTS vault"):
            c.execute(text(stmt))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--database-url", default=None, help="database to (re)create (default: rci_ci on the configured server)")
    p.add_argument("--keep", action="store_true", help="reuse the database if it exists (skip drop / create / seeding)")
    p.add_argument("--update-baseline", action="store_true")
    p.add_argument("--out-dir", default=str(ROOT / "var" / "out" / "backtest"))
    args = p.parse_args()

    os.environ.setdefault("RCI_STORAGE_BACKEND", "local")
    os.environ.setdefault("RCI_STORAGE_LOCAL_ROOT", tempfile.mkdtemp(prefix="rci-ci-blobs-"))
    os.environ["RCI_LLM_PROVIDER"] = "none"
    os.environ["RCI_EMBEDDING_PROVIDER"] = "local"
    from sqlalchemy.engine import make_url

    from rci.config import get_settings

    url = args.database_url or make_url(get_settings().database_url).set(database="rci_ci").render_as_string(hide_password=False)
    os.environ["RCI_DATABASE_URL"] = url
    get_settings.cache_clear()
    from rci.cli import main as rci

    steps = []
    if not args.keep:
        recreate(url)
        steps += [["migrate"], ["seed"], ["seed-precedents", "--n", str(POOL), "--seed", str(SEED), "--prefix", PREFIX],
                  ["holdouts", "select", "--data-class", "synthetic", "--prefix", PREFIX, "--n", str(HOLDOUTS), "--seed", str(SEED)],
                  ["clauses", "mine", "--data-class", "synthetic"]]
    gate = ["--update-baseline"] if args.update_baseline else ["--gate"]
    steps.append(["backtest", "--data-class", "synthetic", "--prefix", PREFIX, "--out-dir", args.out_dir, *gate])
    for step in steps:
        print(f"$ rci {' '.join(step)}", file=sys.stderr, flush=True)
        rc = rci(["--database-url", url, *step])
        if rc:
            print(f"step failed ({rc}): rci {' '.join(step)}", file=sys.stderr)
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
