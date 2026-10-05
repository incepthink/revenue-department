"""Intelligence-engine configuration: config/intel.yaml (defaults) overlaid with config/intel_tuned.yaml (written by
`rci tune --save`). The fingerprint of the effective config is stored with every analysis."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from rci.config import get_settings

INTEL_ENGINE_VERSION = "intel.1.0"
FEATURES = ("sim_facts", "sim_documents", "sim_issues", "tenure", "location", "area", "regime", "recency", "validations", "gaps")
TUNABLE = ("weights", "support_threshold")


@dataclass
class IntelConfig:
    raw: dict
    tuned: dict | None = None  # the tuned file's metadata block, when one was applied

    @classmethod
    def load(cls, path: Path | None = None, tuned_path: Path | None = None, data_class: str | None = None) -> "IntelConfig":
        """`data_class`: the case being analysed. Weights tuned on one data class (e.g. the synthetic pool) are not
        applied to cases of another (real cases keep the defaults until tuned on real precedents)."""
        s = get_settings()
        raw = yaml.safe_load((path or s.intel_file).read_text(encoding="utf-8"))
        tuned_path = tuned_path or s.intel_tuned_file
        meta = None
        if tuned_path.exists():
            t = yaml.safe_load(tuned_path.read_text(encoding="utf-8")) or {}
            tuned_on = (t.get("tuned") or {}).get("data_class")
            if tuned_on and data_class and tuned_on != data_class:
                return cls(raw, None)
            if t.get("weights"):
                raw["weights"] = {**raw["weights"], **{k: float(v) for k, v in t["weights"].items() if k in FEATURES}}
            if t.get("support_threshold") is not None:
                raw["support_threshold"] = float(t["support_threshold"])
            meta = t.get("tuned")
        return cls(raw, meta)

    def with_overrides(self, weights: dict | None = None, support_threshold: float | None = None) -> "IntelConfig":
        raw = copy.deepcopy(self.raw)
        if weights:
            raw["weights"] = {**raw["weights"], **weights}
        if support_threshold is not None:
            raw["support_threshold"] = support_threshold
        return IntelConfig(raw, self.tuned)

    @property
    def weights(self) -> dict[str, float]:
        return {k: float(self.raw["weights"].get(k, 0.0)) for k in FEATURES}

    @property
    def support_threshold(self) -> float:
        return float(self.raw["support_threshold"])

    @property
    def min_support_cases(self) -> int:
        return int(self.raw.get("min_support_cases", 2))

    @property
    def step_threshold(self) -> float:
        return float(self.raw.get("step_threshold", 0.4))

    @property
    def top_k(self) -> int:
        return int(self.raw["recall"]["top_k"])

    @property
    def per_facet_k(self) -> int:
        return int(self.raw["recall"]["per_facet_k"])

    @property
    def filters(self) -> dict:
        return self.raw["filters"]

    @property
    def explain_with_llm(self) -> bool:
        return bool(self.raw.get("explanations", {}).get("llm", True))

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]

    def source(self) -> str:
        return "tuned" if self.tuned else "default"
