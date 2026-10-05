"""Authentication and roles.

* Passwords: scrypt (stdlib), stored as `scrypt$n$r$p$salt_b64$hash_b64` in users.password_hash.
* Tokens: HS256 JWT (sub = user id, role, exp), issued by POST /auth/login, sent as `Authorization: Bearer`.
  The web app keeps the token in an httpOnly cookie and forwards it through its /api proxy.
* Roles: reviewer (review cards), officer (read cases, file incoming dockets, authorised draft rendering),
  admin (everything, including rules / ASR / users). `require(...)` lists the roles allowed on a route;
  admin is always allowed.
* Single-user mode: the web app has no login. A request without a bearer token acts as the built-in operator user
  (admin role, created on first use). Tokens are still honoured for scripts and tests.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import os
import uuid
from dataclasses import dataclass
from typing import Callable

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from rci.config import get_settings

ROLES = ("reviewer", "officer", "admin")
_SCRYPT = (2**14, 8, 1)


class AuthConfigError(RuntimeError):
    pass


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    n, r, p = _SCRYPT
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, n, r, p, salt, digest = stored.split("$")
    got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    return hmac.compare_digest(got, base64.b64decode(digest))


def _secret() -> str:
    s = get_settings().auth_secret
    if not s or "REPLACE" in s:
        raise AuthConfigError("RCI_AUTH_SECRET not set — run `rci init-env` (adds missing keys) or `rci gen-keys`")
    if len(s.encode()) < 32:
        raise AuthConfigError("RCI_AUTH_SECRET must be at least 32 bytes")
    return s


def issue_token(user_id: uuid.UUID, role: str, ttl_minutes: int | None = None) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    ttl = ttl_minutes or get_settings().auth_token_ttl_minutes
    payload = {"sub": str(user_id), "role": role, "iat": now, "exp": now + dt.timedelta(minutes=ttl)}
    return jwt.encode(payload, _secret(), algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, _secret(), algorithms=["HS256"], options={"require": ["sub", "exp"]})


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    email: str
    display_name: str
    role: str

    def has(self, *roles: str) -> bool:
        return self.role == "admin" or self.role in roles

    def to_json(self) -> dict:
        return {"id": str(self.id), "email": self.email, "display_name": self.display_name, "role": self.role}


def authenticate(session: Session, email: str, password: str):
    from rci.db.models import User

    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return None
    user.last_login_at = dt.datetime.now(dt.timezone.utc)
    return user


OPERATOR_EMAIL = "operator@rci.local"


def operator_user(session: Session):
    """The single built-in platform user (admin role), created on first use. Requests without a token act as it."""
    from rci.db.models import User

    user = session.scalar(select(User).where(User.email == OPERATOR_EMAIL))
    if user is None:
        user = User(email=OPERATOR_EMAIL, display_name="Operator", role="admin", password_hash=None)
        session.add(user)
        session.flush()
    return user


def user_from_request(request: Request, session: Session) -> CurrentUser:
    from rci.db.models import User

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        op = operator_user(session)
        cu = CurrentUser(op.id, op.email, op.display_name, op.role)
        request.state.user = cu  # read by the audit middleware
        return cu
    try:
        claims = decode_token(header[7:].strip())
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    user = session.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(401, "user disabled")
    cu = CurrentUser(user.id, user.email, user.display_name, user.role)
    request.state.user = cu  # read by the audit middleware
    return cu


def require(*roles: str) -> Callable:
    """Dependency factory: the caller must hold one of `roles` (admin always passes)."""
    from rci.api.deps import get_session as _gs

    def dep(request: Request, session: Session = Depends(_gs)) -> CurrentUser:
        user = user_from_request(request, session)
        if roles and not user.has(*roles):
            raise HTTPException(403, f"requires role: {' / '.join(roles)}")
        return user

    return dep


def create_user(session: Session, *, email: str, display_name: str, role: str, password: str,
                office_code: str | None = "PILOT"):
    from rci.db.models import Office, User

    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    email = email.strip().lower()
    if session.scalar(select(User).where(User.email == email)):
        raise ValueError(f"user {email} exists")
    office = session.scalar(select(Office).where(Office.code == office_code)) if office_code else None
    user = User(email=email, display_name=display_name, role=role, password_hash=hash_password(password),
                office_id=office.id if office else None)
    session.add(user)
    session.flush()
    return user
