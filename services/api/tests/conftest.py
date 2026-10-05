from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path

import pytest

# Deterministic test keys; never used outside tests.
os.environ.setdefault("RCI_VAULT_KEY_B64", base64.b64encode(b"k" * 32).decode())
os.environ.setdefault("RCI_VAULT_HMAC_KEY_B64", base64.b64encode(b"h" * 32).decode())
os.environ["RCI_AUTH_SECRET"] = "test-secret-not-for-production-0123456789"
os.environ["RCI_EMBEDDING_PROVIDER"] = "local"
os.environ["RCI_LLM_PROVIDER"] = "none"
os.environ["RCI_OCR_PRIMARY"] = "none"
os.environ["RCI_OCR_FALLBACK"] = "none"
os.environ["RCI_PIPELINE_MODE"] = "inline"

from rci.config import REPO_ROOT, Parameters, Settings, get_settings  # noqa: E402
from rci.pipeline.redact.engine import Redactor  # noqa: E402
from rci.pipeline.redact.vault import InMemoryVaultStore, Vault  # noqa: E402

SAMPLES = REPO_ROOT / "eval" / "samples"


@pytest.fixture
def vault() -> Vault:
    return Vault(InMemoryVaultStore(), b"k" * 32, b"h" * 32, "test")


@pytest.fixture
def redactor(vault) -> Redactor:
    return Redactor(vault)


@pytest.fixture
def case_id() -> uuid.UUID:
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="session")
def params() -> Parameters:
    return Parameters.load(REPO_ROOT / "config" / "parameters.yaml")


# ---------------------------------------------------------------------------------------------
# Database fixtures (tests marked `db`): a dedicated rci_test database, migrated and seeded once.
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def db_url(tmp_path_factory) -> str:
    from sqlalchemy import create_engine, text

    url = Settings().test_database_url
    try:
        with create_engine(url).connect() as c:
            c.execute(text("select 1"))
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"test database unavailable ({e}); run `docker compose up -d db`")
    blobs = tmp_path_factory.mktemp("blobs")
    os.environ["RCI_DATABASE_URL"] = url
    os.environ["RCI_STORAGE_BACKEND"] = "local"
    os.environ["RCI_STORAGE_LOCAL_ROOT"] = str(blobs)
    get_settings.cache_clear()
    from rci.db.session import get_engine

    get_engine.cache_clear()
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "services" / "api" / "alembic.ini"))
    cfg.attributes["url"] = url
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    from rci.db.session import session_scope
    from rci.seed import seed_all

    with session_scope(url) as s:
        seed_all(s)
    return url


@pytest.fixture
def db_session(db_url):
    from rci.db.session import session_factory

    s = session_factory(db_url)()
    try:
        yield s
        s.commit()
    finally:
        s.close()


def seed_stack(on):
    """The seeded rules (rules.yaml + requirement_catalog.yaml) in force on `on`, as rci.rules.stack_for would load
    them from a freshly seeded database — for unit tests without one."""
    import datetime as dt

    from rci.rules.resolve import CodedRow, RuleSetRow, build_stack
    from rci.seed import _load

    def d(v):
        return dt.date.fromisoformat(v) if isinstance(v, str) else v

    catalog = {r["code"]: r for r in _load("requirement_catalog.yaml")["requirements"]}
    specs = _load("rules.yaml")["rule_sets"]
    sets = [RuleSetRow(r["code"], r["kind"], d(r["effective_from"]), d(r.get("effective_to")), d(r.get("notification_date")),
                       r.get("supersedes"), r.get("status", "to_verify"), r["name"]) for r in specs]
    reqs = [CodedRow(r["code"], q["requirement_code"], q.get("condition") or {}, q.get("status", "to_verify"),
                     {"mandatory": q.get("mandatory", True), "source_ref": q.get("source_ref"),
                      **{k: catalog[q["requirement_code"]].get(k) for k in ("name_en", "name_mr", "category", "intake_slot")}})
            for r in specs for q in r.get("requirements", [])]
    checks = [CodedRow(r["code"], c["code"], c.get("condition") or {}, c.get("status", "to_verify"),
                       {"slot": c["slot"], "text_en": c["text_en"], "text_mr": c.get("text_mr"),
                        "severity": c.get("severity", "problem"), "source_ref": c.get("source_ref"),
                        "condition": c.get("condition") or {}})
              for r in specs for c in r.get("document_checks", [])]
    return build_stack(on, sets, requirements=reqs, document_checks=checks)


def sample_files(case_ref: str) -> list[tuple[str, bytes]]:
    folder = SAMPLES / "dockets" / case_ref
    return [(f.name, f.read_bytes()) for f in sorted(Path(folder).glob("*.pdf"))]


# ---------------------------------------------------------------------------------------------
# Users and an authenticated API client (Phase 2)
# ---------------------------------------------------------------------------------------------
PASSWORD = "test-password-1"
USERS = {
    "admin": ("admin@test.local", "admin"),
    "reviewer1": ("reviewer1@test.local", "reviewer"),
    "reviewer2": ("reviewer2@test.local", "reviewer"),
    "officer": ("officer@test.local", "officer"),
}


@pytest.fixture(scope="session")
def users(db_url) -> dict:
    """name -> CurrentUser, created once in the test database."""
    from rci.auth import CurrentUser, create_user
    from rci.db.session import session_scope

    out = {}
    with session_scope(db_url) as s:
        for name, (email, role) in USERS.items():
            u = create_user(s, email=email, display_name=name, role=role, password=PASSWORD)
            out[name] = CurrentUser(u.id, u.email, u.display_name, u.role)
    return out


class ApiClient:
    """TestClient wrapper that logs in as one of USERS and sends the bearer token."""

    def __init__(self, name: str | None):
        from fastapi.testclient import TestClient

        from rci.api.main import app

        self.client = TestClient(app)
        self.headers = {}
        if name:
            r = self.client.post("/auth/login", json={"email": USERS[name][0], "password": PASSWORD})
            assert r.status_code == 200, r.text
            self.headers = {"Authorization": f"Bearer {r.json()['token']}"}

    def __getattr__(self, method):
        fn = getattr(self.client, method)
        return lambda url, **kw: fn(url, headers={**self.headers, **kw.pop("headers", {})}, **kw)


@pytest.fixture
def api(users):
    return ApiClient


def synthetic_card(i: int, *, seed: int = 7, case_ref: str | None = None) -> tuple[dict, dict]:
    """(approved-style card, docket exhibits) of synthetic precedent i, built without a database (Phase 4 unit tests)."""
    import datetime as dt
    import random

    from rci.intel.synthetic import _scenario, build_docket
    from rci.pipeline.card import CardExhibit, assemble_card, doc_date_of
    from rci.pipeline.validators import ExhibitFacts, run_validators
    from rci.seed import _load

    ref = case_ref or f"UNIT-{i:03d}"
    rng = random.Random(f"{seed}:{i}")
    start, end = dt.date(2019, 6, 1), dt.date(2026, 3, 31)
    sc = _scenario(rng, i, start, (end - start).days)
    docket, item_codes, _ = build_docket(ref, sc, rng)
    case_id = uuid.uuid5(uuid.NAMESPACE_URL, ref)
    exs = []
    for ex in docket.exhibits:
        eid = str(uuid.uuid5(case_id, f"{ex['type']}:{ex['seq']}"))
        ex["exhibit_id"] = eid
        exs.append(CardExhibit(ExhibitFacts(eid, ex["seq"], ex["type"], ex["stage"], ex["fields"]), [ex["seq"]], 1.0, "manual",
                               doc_date_of(ex["type"], ex["fields"])))
    reqs = [{"applies_when": {}, **r} for r in _load("requirement_catalog.yaml")["requirements"] if r.get("active", True)]
    events = _load("event_catalog.yaml")["events"]
    q_ex = next((e.facts.exhibit_id for e in exs if e.facts.exhibit_type == "QUERY_LETTER"), None)
    known = {r["code"] for r in reqs}
    card, _, _ = assemble_card(
        case={"case_id": str(case_id), "case_ref": ref, "office_code": "PILOT", "data_class": "synthetic",
              "case_type": "CHS_CLASS2_TO_CLASS1"},
        exhibits=exs, validations=run_validators([e.facts for e in exs], Parameters.load(REPO_ROOT / "config" / "parameters.yaml")),
        requirement_catalog=reqs, event_catalog=events, pipeline_version="unit",
        item_codes=lambda qid, idx, it: [c for c in item_codes.get(idx, []) if c in known] if qid == q_ex else [])
    for q in card["facts"]["queries"]:
        for n, it in enumerate(q["items"]):
            it["requirement_codes"] = [c for c in item_codes.get(n, []) if c in known]
            it["mapping"] = "codes" if it["requirement_codes"] else "not_requirement"
    card["status"] = "approved"
    return card, {e["exhibit_id"]: e for e in docket.exhibits}


def ingest_case(session, case_ref: str, sample: str):
    """Ingest a sample docket into the test DB under a new case ref."""
    from rci.pipeline.runner import PipelineContext, add_document, get_or_create_case, process_case

    ctx = PipelineContext.default()
    case, _ = get_or_create_case(session, case_ref=case_ref, data_class="synthetic")
    for name, data in sample_files(sample):
        add_document(session, ctx.blobs, case, name, data)
    process_case(session, case.id, ctx)
    session.commit()
    return case

