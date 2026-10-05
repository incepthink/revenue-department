"""CI gate: a backtest run passes when every absolute check holds and no metric regressed past its tolerance against
the committed baseline (eval/backtest/baseline.json). Exit codes of `rci backtest --gate`: 0 pass, 1 fail.

A baseline is only comparable with a run on the same holdout set (holdouts_hash); a changed set fails the gate until a
new baseline is written deliberately (`rci backtest --update-baseline`). Metrics that are None on either side (e.g.
P@5 before any officer relevance label exists) are reported as skipped, never as passed.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from rci.config import get_settings

BASELINE_METRICS = ("n_holdouts", "query_precision", "query_recall", "query_f1", "first_letter_completeness",
                    "office_first_letter_completeness", "complete_first_letter_rate", "statutory_false_negatives",
                    "adjudicated_precision", "steps_precision", "steps_recall", "steps_f1", "kendall_tau", "fee_coverage",
                    "fee_exact_match_rate", "fee_mean_abs_pct_error", "p_at_5", "ndcg_at_5", "proxy_ndcg_at_5",
                    "draft_render_ok_rate", "drafts_complete_rate", "leak_failures", "draft_rating_mean", "holdout_leaks")


def baseline_path(gate_cfg: dict) -> Path:
    p = Path(gate_cfg.get("baseline_file", "eval/backtest/baseline.json"))
    return p if p.is_absolute() else get_settings().repo_root / p


def load_baseline(gate_cfg: dict) -> dict | None:
    p = baseline_path(gate_cfg)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def write_baseline(result: dict, gate_cfg: dict) -> Path:
    p = baseline_path(gate_cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"written_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "run_id": result.get("run_id"),
           "code_version": result.get("code_version"), "data_class": result["config"]["data_class"],
           "holdouts": result["config"]["holdouts"], "holdouts_hash": result["config"]["holdouts_hash"],
           "intel": result["config"]["intel"], "fee_world": result["config"]["fee_world"],
           "metrics": {k: result["metrics"].get(k) for k in BASELINE_METRICS}}
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def evaluate(result: dict, baseline: dict | None, gate_cfg: dict) -> dict:
    m = result["metrics"]
    checks = []

    def add(name, value, ok, rule, reason=""):
        checks.append({"check": name, "value": value, "rule": rule, "status": "pass" if ok is True else "fail" if ok is False else "skipped",
                       "reason": reason})

    n_min = int(gate_cfg.get("min_holdouts", 0))
    add("min_holdouts", m.get("n_holdouts"), (m.get("n_holdouts") or 0) >= n_min, f">= {n_min}")
    if gate_cfg.get("fail_on_tuning_leak", True):
        add("tuning_leak", m.get("tuning_leak"), not m.get("tuning_leak"), "== 0", "tuned weights must not have seen a holdout")
    add("errors", m.get("errors"), not m.get("errors"), "== 0", "every holdout must replay")
    for name, spec in (gate_cfg.get("absolute") or {}).items():
        v = m.get(name)
        if v is None:
            add(name, v, None, str(spec), "not measured in this run")
        elif "max" in spec:
            add(name, v, v <= spec["max"], f"<= {spec['max']}")
        else:
            add(name, v, v >= spec["min"], f">= {spec['min']}")
    if baseline is None:
        add("baseline", None, False, "present", "no baseline — run `rci backtest --update-baseline` and commit it")
    elif baseline.get("holdouts_hash") != result["config"]["holdouts_hash"]:
        add("baseline", baseline.get("holdouts_hash"), False, f"== {result['config']['holdouts_hash']}",
            "holdout set differs from the baseline's — re-baseline deliberately")
    elif baseline.get("data_class") != result["config"]["data_class"]:
        add("baseline", baseline.get("data_class"), False, result["config"]["data_class"], "baseline is for another data class")
    else:
        bm = baseline.get("metrics") or {}
        for name, spec in (gate_cfg.get("regression") or {}).items():
            v, b = m.get(name), bm.get(name)
            tol = float(spec.get("tolerance", 0))
            if v is None or b is None:
                add(name, v, None, f"baseline {b}", "not measured on both sides")
            elif spec.get("direction", "higher") == "higher":
                add(name, v, v >= b - tol - 1e-9, f">= {b} - {tol}")
            else:
                add(name, v, v <= b + tol + 1e-9, f"<= {b} + {tol}")
    failed = [c for c in checks if c["status"] == "fail"]
    return {"passed": not failed, "failed": [c["check"] for c in failed], "checks": checks,
            "baseline": {k: baseline.get(k) for k in ("written_at", "run_id", "holdouts_hash", "code_version")} if baseline else None}
