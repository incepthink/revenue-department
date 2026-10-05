"""Login and user administration."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from rci.api.deps import get_session
from rci.auth import ROLES, CurrentUser, authenticate, create_user, hash_password, issue_token, require
from rci.db.models import User
from rci.pipeline.runner import audit

router = APIRouter()
ADMIN = require("admin")


class Login(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(body: Login, request: Request, session: Session = Depends(get_session)) -> dict:
    user = authenticate(session, body.email, body.password)
    ip = request.client.host if request.client else None
    if user is None:
        audit(session, "auth.login_failed", actor_kind="anonymous", email=body.email.strip().lower()[:320], ip=ip)
        session.commit()
        raise HTTPException(401, "invalid email or password")
    audit(session, "auth.login", actor_kind="user", entity_type="user", entity_id=user.id, ip=ip)
    return {"token": issue_token(user.id, user.role), "user": CurrentUser(user.id, user.email, user.display_name, user.role).to_json()}


@router.get("/auth/me")
def me(user: CurrentUser = Depends(require())) -> dict:
    return user.to_json()


def _user_out(u: User) -> dict:
    return {"id": str(u.id), "email": u.email, "display_name": u.display_name, "role": u.role, "is_active": u.is_active,
            "last_login_at": u.last_login_at, "created_at": u.created_at}


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=200)
    role: str
    password: str = Field(min_length=8)


class UserPatch(BaseModel):
    display_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)


@router.get("/admin/users")
def list_users(session: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)) -> list[dict]:
    return [_user_out(u) for u in session.scalars(select(User).order_by(User.created_at))]


@router.post("/admin/users", status_code=201)
def add_user(body: UserCreate, session: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)) -> dict:
    try:
        u = create_user(session, email=body.email, display_name=body.display_name, role=body.role, password=body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(session, "user.create", actor_kind="user", entity_type="user", entity_id=u.id, actor=str(user.id), role=u.role)
    return _user_out(u)


@router.patch("/admin/users/{user_id}")
def patch_user(user_id: uuid.UUID, body: UserPatch, session: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)) -> dict:
    u = session.get(User, user_id)
    if u is None:
        raise HTTPException(404, "user not found")
    if body.role is not None and body.role not in ROLES:
        raise HTTPException(400, f"role must be one of {ROLES}")
    if u.id == user.id and (body.role not in (None, "admin") or body.is_active is False):
        raise HTTPException(400, "you cannot demote or disable yourself")
    changes = body.model_dump(exclude_none=True)
    for k in ("display_name", "role", "is_active"):
        if k in changes:
            setattr(u, k, changes[k])
    if body.password:
        u.password_hash = hash_password(body.password)
    audit(session, "user.update", actor_kind="user", entity_type="user", entity_id=u.id, actor=str(user.id),
          fields=sorted(k for k in changes if k != "password") + (["password"] if body.password else []))
    return _user_out(u)
