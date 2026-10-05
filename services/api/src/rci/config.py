"""Settings (env / .env, prefix RCI_) and department parameters (config/parameters.yaml)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_repo_root() -> Path:
    env = os.environ.get("RCI_REPO_ROOT")
    if env:
        return Path(env)
    # services/api/src/rci/config.py -> repo root
    return Path(__file__).resolve().parents[4]


REPO_ROOT = _default_repo_root()

AZURITE_CONN = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RCI_", env_file=str(REPO_ROOT / ".env"), extra="ignore", populate_by_name=True
    )

    repo_root: Path = REPO_ROOT
    database_url: str = "postgresql+psycopg://rci:rci@localhost:5434/rci"
    test_database_url: str = "postgresql+psycopg://rci:rci@localhost:5434/rci_test"
    redis_url: str = "redis://localhost:6379/0"

    storage_backend: Literal["local", "azurite", "azure"] = "local"
    storage_local_root: Path = REPO_ROOT / "var" / "blobs"
    azure_storage_connection_string: str = AZURITE_CONN
    blob_container_prefix: str = "rci"

    pipeline_mode: Literal["inline", "queue"] = "inline"
    raster_dpi: int = 300

    # PII vault
    vault_key_b64: str | None = None
    vault_hmac_key_b64: str | None = None
    vault_key_id: str = "dev-1"

    # LLM: direct OpenAI API calls. The key is read from OPENAI_API_KEY (RCI_OPENAI_API_KEY also accepted).
    llm_provider: Literal["none", "openai"] = "openai"
    openai_api_key: str | None = Field(default=None, validation_alias=AliasChoices("OPENAI_API_KEY", "RCI_OPENAI_API_KEY"))
    openai_model: str = "gpt-6-luna"
    openai_ocr_model: str | None = None  # the library line reader; default openai_model
    allowed_real_data_regions: list[str] = ["centralindia"]  # Azure Document Intelligence OCR only

    # OCR
    ocr_primary: Literal["azure_di", "tesseract", "none"] = "none"
    ocr_fallback: Literal["llm_vision", "none"] = "none"
    ocr_fallback_threshold: float = 0.80
    azure_di_endpoint: str | None = None
    azure_di_key: str | None = None
    azure_di_region: str = "centralindia"
    azure_di_model: Literal["prebuilt-read", "prebuilt-layout"] = "prebuilt-read"
    azure_di_api_version: str = "2024-11-30"

    # Embeddings (case facets). local = deterministic hashing embedder (no network); openai = OpenAI API with
    # the same key as the LLM. Switching changes the vector model, so precedents must be re-embedded.
    embedding_provider: Literal["local", "openai"] = "local"
    openai_embedding_model: str = "text-embedding-3-small"

    # GR library (Part 2): department documents, OCR'd with geometry, split into provisions, hybrid search.
    library_root: Path = REPO_ROOT / "docs" / "provided-by-department"
    library_inventory_file: Path = REPO_ROOT / "config" / "library" / "inventory.yaml"
    library_raster_dpi: int = 200
    library_tile_min_px: int = 700  # a reader tile is at least this tall (px) where the page allows
    library_reader_variant: Literal["A", "B"] = "A"  # A numbered line boxes | B plain transcription aligned to boxes
    library_embedding_provider: Literal["local", "openai"] = "openai"  # falls back to local without an API key
    library_embedding_model: str = "text-embedding-3-large"  # sent with dimensions=1536

    # Auth: HS256 bearer tokens issued by POST /auth/login. The web app keeps the token in an httpOnly cookie.
    auth_secret: str | None = None
    auth_token_ttl_minutes: int = 480
    cors_origins: list[str] = ["http://localhost:3000"]

    parameters_file: Path = REPO_ROOT / "config" / "parameters.yaml"
    fees_file: Path = REPO_ROOT / "config" / "fees.yaml"  # fee-engine parameters (Phase 3)
    intel_file: Path = REPO_ROOT / "config" / "intel.yaml"  # matching / gaps / steps defaults (Phase 3)
    intel_tuned_file: Path = REPO_ROOT / "config" / "intel_tuned.yaml"  # written by `rci tune --save`
    drafting_file: Path = REPO_ROOT / "config" / "drafting.yaml"  # office format / wording items (Phase 4)
    backtest_file: Path = REPO_ROOT / "config" / "backtest.yaml"  # holdouts, metrics, CI gate (Phase 4)
    schemas_dir: Path = REPO_ROOT / "packages" / "schemas"
    output_dir: Path = REPO_ROOT / "var" / "out"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@dataclass(frozen=True)
class Parameter:
    key: str
    value: Any
    status: Literal["verified", "to_verify"]
    source: str | None = None
    notes: str | None = None


class Parameters:
    """Department / engineering parameters. Every value carries a verification status so that
    downstream output (validators, fee engine) can flag results that depend on unverified values."""

    def __init__(self, items: dict[str, Parameter]):
        self._items = items

    @classmethod
    def load(cls, path: Path | None = None) -> "Parameters":
        path = path or get_settings().parameters_file
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        items = {}
        for key, spec in raw.get("parameters", {}).items():
            items[key] = Parameter(
                key=key,
                value=spec["value"],
                status=spec.get("status", "to_verify"),
                source=spec.get("source"),
                notes=spec.get("notes"),
            )
        return cls(items)

    def get(self, key: str) -> Parameter:
        return self._items[key]

    def value(self, key: str) -> Any:
        return self._items[key].value

    def keys(self) -> list[str]:
        return list(self._items)
