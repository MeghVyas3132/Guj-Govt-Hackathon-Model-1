"""Resolving the caller to a Principal.

Both humans (JWT) and integrations (API key) end up as the same object, so an
endpoint cannot accidentally authorise one differently from the other.
"""

from datetime import UTC, datetime

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.enums import ROLE_SCOPES, ActorType, Role
from app.core.security import decode_token, verify_secret
from app.models.user import ApiKey, User
from app.schemas.auth import Principal

_UNAUTHENTICATED = HTTPException(
    status_code=401,
    detail="Supply a Bearer token or an X-API-Key header.",
    headers={"WWW-Authenticate": "Bearer"},
)


async def resolve_principal(
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Principal:
    if x_api_key:
        return await _from_api_key(session, x_api_key)
    if authorization and authorization.lower().startswith("bearer "):
        return await _from_token(session, authorization.split(" ", 1)[1])
    raise _UNAUTHENTICATED


async def _from_token(session: AsyncSession, token: str) -> Principal:
    try:
        claims = decode_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc

    if claims.get("type") != "access":
        raise HTTPException(
            status_code=401, detail="Refresh tokens cannot authorise requests."
        )

    user = await session.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive.")

    # Role comes from the row, not the token: revoking a role must take effect
    # before the token expires.
    role = Role(user.role)
    return Principal(
        actor_type=ActorType.USER,
        actor_id=str(user.id),
        role=role,
        department_id=user.department_id,
        scopes=ROLE_SCOPES[role],
        label=user.email,
    )


async def _from_api_key(session: AsyncSession, raw_key: str) -> Principal:
    candidates = (
        await session.execute(select(ApiKey).where(ApiKey.key_prefix == raw_key[:12]))
    ).scalars().all()
    now = datetime.now(UTC)

    for candidate in candidates:
        if not verify_secret(raw_key, candidate.key_hash):
            continue
        if candidate.revoked_at is not None:
            raise HTTPException(status_code=401, detail="API key has been revoked.")
        if candidate.expires_at is not None and candidate.expires_at < now:
            raise HTTPException(status_code=401, detail="API key has expired.")

        candidate.last_used_at = now
        await session.flush()
        return Principal(
            actor_type=ActorType.API_KEY,
            actor_id=str(candidate.id),
            role=Role.DEPT_ADMIN,
            department_id=candidate.department_id,
            # An API key's scopes are its own, not its role's: an integration that
            # only needs to read should not be able to write because the role it
            # maps to could.
            scopes=frozenset(candidate.scopes or []),
            label=f"{candidate.name} ({candidate.key_prefix}…)",
        )

    raise HTTPException(status_code=401, detail="Unknown API key.")


def require_scope(scope: str):
    """Router-level guard. Row-level scoping still happens in the repositories."""

    async def guard(principal: Principal = Depends(resolve_principal)) -> Principal:
        if not principal.can(scope):
            raise HTTPException(
                status_code=403,
                detail=f"This action requires the {scope!r} scope.",
            )
        return principal

    return guard


async def request_context(request: Request) -> dict[str, str | None]:
    """Client IP and user agent for the audit trail."""
    return {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


def get_enricher() -> "StreamEnricher":
    """The stream enricher, as a dependency so tests can substitute one.

    Built per request rather than shared: it holds no state worth reusing, and a
    module-level instance would make the ffprobe path unpatchable.
    """
    from app.services.enrichment import StreamEnricher

    return StreamEnricher()
