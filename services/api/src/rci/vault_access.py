"""The only door into the PII vault.

Detokenisation (token → real name / Aadhaar / phone ...) is allowed for exactly one purpose: rendering an
officer's draft (Phase 4). It needs the officer or admin role, is limited to the tokens in the text being
rendered, and every call is audited with the purpose, the draft reference and the token types (never the values).
Review screens show tokens. Phase 4: the issue render of a draft is detokenised on download (drafting.service.download),
officer / admin only; the stored draft keeps the tokens.
"""
from __future__ import annotations

import uuid
from collections import Counter

from sqlalchemy.orm import Session

from rci.auth import CurrentUser
from rci.pipeline.redact.vault import TOKEN_RE, SqlVaultStore, Vault
from rci.pipeline.runner import audit

ALLOWED_PURPOSES = ("draft_render",)
ALLOWED_ROLES = ("officer",)  # + admin


class VaultAccessDenied(PermissionError):
    pass


def render_with_pii(session: Session, case_id: uuid.UUID, text: str, user: CurrentUser, *, purpose: str,
                    draft_ref: str, escape=None) -> str:
    """`escape` is applied to each detokenised value (e.g. XML escaping when `text` is a docx part)."""
    if purpose not in ALLOWED_PURPOSES:
        audit(session, "vault.denied", actor_kind="user", case_id=case_id, actor=str(user.id), purpose=purpose, reason="purpose")
        raise VaultAccessDenied(f"vault access is only for {ALLOWED_PURPOSES}")
    if not user.has(*ALLOWED_ROLES):
        audit(session, "vault.denied", actor_kind="user", case_id=case_id, actor=str(user.id), purpose=purpose, reason="role")
        raise VaultAccessDenied("vault access needs the officer or admin role")
    vault = Vault.from_settings(SqlVaultStore(session))
    types: Counter = Counter()

    def sub(m):
        types[m.group("type")] += 1
        value = vault.detokenize(case_id, m.group(0))
        return escape(value) if escape else value

    out = TOKEN_RE.sub(sub, text)
    audit(session, "vault.detokenize", actor_kind="user", case_id=case_id, actor=str(user.id), purpose=purpose,
          draft_ref=draft_ref, token_types=dict(types))
    return out
