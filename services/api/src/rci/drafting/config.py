"""config/drafting.yaml: office format items (each {value, status}) and drafting engineering settings."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from rci.config import get_settings

DRAFTING_VERSION = "draft.1.0"


@dataclass
class DraftingConfig:
    raw: dict

    @classmethod
    def load(cls, path: Path | None = None) -> "DraftingConfig":
        return cls(yaml.safe_load((path or get_settings().drafting_file).read_text(encoding="utf-8")) or {})

    def item(self, path: str) -> tuple[Any, str]:
        """(value, status) of a dotted path; plain values count as engineering settings (status verified)."""
        node: Any = self.raw
        for part in path.split("."):
            node = (node or {}).get(part) if isinstance(node, dict) else None
        if isinstance(node, dict) and "value" in node:
            return node["value"], node.get("status", "to_verify")
        return node, "verified"

    def value(self, path: str, default: Any = None) -> Any:
        v, _ = self.item(path)
        return default if v is None else v

    def unverified(self, paths: list[str]) -> list[str]:
        return [p for p in paths if self.item(p)[1] != "verified"]

    @property
    def templates_dir(self) -> Path:
        p = Path(self.raw.get("templates_dir", "config/templates"))
        return p if p.is_absolute() else get_settings().repo_root / p

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]
