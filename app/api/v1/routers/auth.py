"""Login, identity, and the public keys other services verify tokens with."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import resolve_principal
from app.core.enums import ROLE_SCOPES, ActorType, Role
from app.core.security import (
    ACCESS_TTL_SECONDS,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.models.department import Department
from app.models.user import User
from app.schemas.auth import LoginRequest, MeResponse, Principal, TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])

# Identical for an unknown address and a wrong password, so the endpoint cannot be
# used to discover which accounts exist.
_GENERIC_FAILURE = "Incorrect email or password."


def _tokens_for(user: User) -> TokenPair:
    role = Role(user.role)
    return TokenPair(
        access_token=create_access_token(
            subject=str(user.id),
            role=role.value,
            department_id=str(user.department_id) if user.department_id else None,
            scopes=sorted(ROLE_SCOPES[role]),
        ),
        refresh_token=create_refresh_token(subject=str(user.id)),
        expires_in=ACCESS_TTL_SECONDS,
    )


@router.post("/login", response_model=TokenPair, summary="Exchange credentials for tokens")
async def login(
    payload: LoginRequest, session: AsyncSession = Depends(get_session)
) -> TokenPair:
    user = (
        await session.execute(select(User).where(User.email == payload.email.lower()))
    ).scalar_one_or_none()

    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail=_GENERIC_FAILURE)

    return _tokens_for(user)


@router.post("/refresh", response_model=TokenPair, summary="Exchange a refresh token")
async def refresh(
    payload: dict, session: AsyncSession = Depends(get_session)
) -> TokenPair:
    import jwt

    token = payload.get("refresh_token", "")
    try:
        claims = decode_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid refresh token") from exc

    if claims.get("type") != "refresh":
        raise HTTPException(
            status_code=401, detail="An access token cannot be exchanged for tokens."
        )

    user = await session.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive.")
    return _tokens_for(user)


@router.get("/me", response_model=MeResponse, summary="Who am I")
async def me(
    principal: Principal = Depends(resolve_principal),
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    department_code = None
    if principal.department_id:
        department = await session.get(Department, principal.department_id)
        department_code = department.code if department else None

    if principal.actor_type is ActorType.API_KEY:
        return MeResponse(
            id=principal.actor_id,
            email=principal.label or "api-key",
            full_name=principal.label or "API key",
            role=principal.role.value,
            department_id=principal.department_id,
            department_code=department_code,
            scopes=sorted(principal.scopes),
        )

    user = await session.get(User, principal.actor_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return MeResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=principal.role.value,
        department_id=user.department_id,
        department_code=department_code,
        scopes=sorted(principal.scopes),
    )
