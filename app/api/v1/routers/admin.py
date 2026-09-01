"""User, API key and audit administration."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import request_context, require_scope
from app.core.security import generate_api_key, hash_password
from app.models.user import ApiKey, AuditLog, User
from app.schemas.auth import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    Principal,
    UserCreate,
    UserRead,
)
from app.services.audit import AuditService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/users", response_model=UserRead, status_code=201)
async def create_user(
    payload: UserCreate,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
    context: dict = Depends(request_context),
) -> UserRead:
    email = payload.email.lower()
    existing = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="That email is already registered")

    user = User(
        email=email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=payload.role.value,
        department_id=payload.department_id,
    )
    session.add(user)
    await session.flush()
    AuditService(session).record(
        action="user.created", entity_type="user", entity_id=user.id, actor=principal,
        after={"email": user.email, "role": user.role}, **context,
    )
    await session.commit()
    return UserRead.model_validate(user, from_attributes=True)


@router.get("/users", response_model=list[UserRead])
async def list_users(
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[UserRead]:
    rows = (
        await session.execute(select(User).order_by(User.email))
    ).scalars().all()
    return [UserRead.model_validate(r, from_attributes=True) for r in rows]


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
async def create_api_key(
    payload: ApiKeyCreate,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
    context: dict = Depends(request_context),
) -> ApiKeyCreated:
    raw, prefix, hashed = generate_api_key()
    key = ApiKey(
        department_id=payload.department_id,
        name=payload.name,
        key_prefix=prefix,
        key_hash=hashed,
        scopes=payload.scopes,
        rate_limit_tier=payload.rate_limit_tier,
    )
    session.add(key)
    await session.flush()
    AuditService(session).record(
        action="api_key.created", entity_type="api_key", entity_id=key.id,
        actor=principal, after={"name": key.name, "scopes": key.scopes}, **context,
    )
    await session.commit()
    # The raw key is returned here and never again; only the hash is stored.
    return ApiKeyCreated(
        id=key.id, name=key.name, key_prefix=prefix, api_key=raw, scopes=key.scopes
    )


@router.get("/api-keys", response_model=list[ApiKeyRead])
async def list_api_keys(
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[ApiKeyRead]:
    rows = (
        await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    ).scalars().all()
    return [ApiKeyRead.model_validate(r, from_attributes=True) for r in rows]


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: UUID,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
    context: dict = Depends(request_context),
) -> None:
    key = await session.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    key.revoked_at = datetime.now(UTC)
    AuditService(session).record(
        action="api_key.revoked", entity_type="api_key", entity_id=key.id,
        actor=principal, before={"name": key.name}, **context,
    )
    await session.commit()


@router.get("/audit-logs", summary="Platform-wide audit trail")
async def audit_logs(
    entity_type: str | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(200, le=1000),
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    stmt = select(AuditLog).order_by(AuditLog.at.desc()).limit(limit)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(r.id),
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": str(r.entity_id) if r.entity_id else None,
            "actor_type": r.actor_type,
            "actor_label": r.actor_label,
            "before": r.before,
            "after": r.after,
            "at": r.at.isoformat(),
        }
        for r in rows
    ]
