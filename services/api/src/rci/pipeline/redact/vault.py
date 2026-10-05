"""PII vault: every redacted value is stored AES-256-GCM encrypted in vault.pii_tokens and replaced in
text by a stable token like [AADHAAR_3f9a12c4]. The same value in the same case always gets the same
token (HMAC-SHA256 lookup), which keeps re-runs idempotent and lets reviewers see that two mentions
are the same person without seeing who it is. Detokenisation is only for authorised draft rendering.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import uuid
from dataclasses import dataclass
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from rci.config import Settings, get_settings

TOKEN_RE = re.compile(r"\[(?P<type>[A-Z_]+)_(?P<h>[0-9a-f]{8})\]")


class VaultConfigError(RuntimeError):
    pass


@dataclass
class VaultRecord:
    case_id: uuid.UUID
    token: str
    entity_type: str
    value_hmac: bytes
    ciphertext: bytes
    nonce: bytes
    key_id: str


class VaultStore(Protocol):
    def find(self, case_id: uuid.UUID, entity_type: str, value_hmac: bytes) -> VaultRecord | None: ...
    def find_token(self, case_id: uuid.UUID, token: str) -> VaultRecord | None: ...
    def add(self, rec: VaultRecord) -> None: ...


class InMemoryVaultStore:
    def __init__(self):
        self.records: list[VaultRecord] = []

    def find(self, case_id, entity_type, value_hmac):
        return next((r for r in self.records if (r.case_id, r.entity_type, r.value_hmac) == (case_id, entity_type, value_hmac)), None)

    def find_token(self, case_id, token):
        return next((r for r in self.records if r.case_id == case_id and r.token == token), None)

    def add(self, rec):
        self.records.append(rec)


class SqlVaultStore:
    def __init__(self, session):
        self.session = session

    def _to_rec(self, row) -> VaultRecord:
        return VaultRecord(row.case_id, row.token, row.entity_type, row.value_hmac, row.ciphertext, row.nonce, row.key_id)

    def find(self, case_id, entity_type, value_hmac):
        from rci.db.models import PiiToken

        row = (
            self.session.query(PiiToken)
            .filter_by(case_id=case_id, entity_type=entity_type, value_hmac=value_hmac)
            .one_or_none()
        )
        return self._to_rec(row) if row else None

    def find_token(self, case_id, token):
        from rci.db.models import PiiToken

        row = self.session.query(PiiToken).filter_by(case_id=case_id, token=token).one_or_none()
        return self._to_rec(row) if row else None

    def add(self, rec):
        from rci.db.models import PiiToken

        self.session.add(
            PiiToken(
                case_id=rec.case_id, token=rec.token, entity_type=rec.entity_type, value_hmac=rec.value_hmac,
                ciphertext=rec.ciphertext, nonce=rec.nonce, key_id=rec.key_id,
            )
        )
        self.session.flush()


def canonical_value(entity_type: str, value: str) -> str:
    from rci.pipeline.normalise import ascii_digits

    v = ascii_digits(value).strip()
    if entity_type in {"AADHAAR", "AADHAAR_VID", "BANK_ACCOUNT"}:
        return re.sub(r"\D", "", v)
    if entity_type == "PHONE":
        d = re.sub(r"\D", "", v)
        return d[-10:]
    if entity_type in {"EMAIL", "PAN"}:
        return v.lower() if entity_type == "EMAIL" else v.upper()
    return re.sub(r"\s+", " ", v)


class Vault:
    def __init__(self, store: VaultStore, key: bytes, hmac_key: bytes, key_id: str):
        if len(key) != 32:
            raise VaultConfigError("vault key must be 32 bytes (AES-256)")
        self.store, self.key_id = store, key_id
        self._aead = AESGCM(key)
        self._hmac_key = hmac_key

    @classmethod
    def from_settings(cls, store: VaultStore, settings: Settings | None = None) -> "Vault":
        s = settings or get_settings()
        if not s.vault_key_b64 or not s.vault_hmac_key_b64 or "REPLACE" in s.vault_key_b64:
            raise VaultConfigError("RCI_VAULT_KEY_B64 / RCI_VAULT_HMAC_KEY_B64 not set — run `rci gen-keys`")
        return cls(store, base64.b64decode(s.vault_key_b64), base64.b64decode(s.vault_hmac_key_b64), s.vault_key_id)

    def _hmac(self, case_id: uuid.UUID, entity_type: str, canon: str) -> bytes:
        msg = f"{case_id}|{entity_type}|{canon}".encode()
        return hmac.new(self._hmac_key, msg, hashlib.sha256).digest()

    def tokenize(self, case_id: uuid.UUID, entity_type: str, value: str) -> str:
        canon = canonical_value(entity_type, value)
        h = self._hmac(case_id, entity_type, canon)
        existing = self.store.find(case_id, entity_type, h)
        if existing:
            return existing.token
        token = f"[{entity_type}_{h.hex()[:8]}]"
        nonce = os.urandom(12)
        aad = f"{case_id}|{entity_type}".encode()
        ct = self._aead.encrypt(nonce, value.encode("utf-8"), aad)
        self.store.add(VaultRecord(case_id, token, entity_type, h, ct, nonce, self.key_id))
        return token

    def detokenize(self, case_id: uuid.UUID, token: str) -> str:
        rec = self.store.find_token(case_id, token)
        if rec is None:
            raise KeyError(token)
        aad = f"{case_id}|{rec.entity_type}".encode()
        return self._aead.decrypt(rec.nonce, rec.ciphertext, aad).decode("utf-8")


def generate_keys() -> dict[str, str]:
    return {
        "RCI_VAULT_KEY_B64": base64.b64encode(os.urandom(32)).decode(),
        "RCI_VAULT_HMAC_KEY_B64": base64.b64encode(os.urandom(32)).decode(),
        "RCI_AUTH_SECRET": base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("="),
    }
