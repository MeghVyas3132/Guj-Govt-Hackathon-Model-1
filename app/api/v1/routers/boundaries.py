"""Administrative boundaries, and the place aliases that resolve names to them."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import require_scope
from app.models.admin_boundary import AdminBoundary
from app.models.source_connector import PlaceAlias
from app.schemas.auth import Principal

router = APIRouter(prefix="/boundaries", tags=["boundaries"])


class BoundaryRead(BaseModel):
    id: UUID
    name: str
    level: str
    code: str | None = None
    area_km2: float | None = None


class AliasIn(BaseModel):
    alias: str
    source: str = "manual"


class AliasRead(BaseModel):
    id: UUID
    alias: str
    boundary_id: UUID
    source: str


@router.get("", response_model=list[BoundaryRead], summary="List boundaries")
async def list_boundaries(
    level: str = Query("district"),
    q: str | None = Query(
        None,
        description=(
            "Case-insensitive substring of the boundary name. Every other "
            "listing endpoint takes `q`, and a caller who assumes this one does "
            "too would otherwise get the whole list back and quietly use the "
            "wrong district -- Gujarat spells several of them with a space "
            "(`Sabar Kantha`, `Panch Mahals`), so exact-match guessing fails."
        ),
    ),
    session: AsyncSession = Depends(get_session),
) -> list[BoundaryRead]:
    stmt = (
        select(
            AdminBoundary.id,
            AdminBoundary.name,
            AdminBoundary.level,
            AdminBoundary.code,
            (func.ST_Area(AdminBoundary.geom) / 1_000_000).label("area_km2"),
        )
        .where(AdminBoundary.level == level)
        .order_by(AdminBoundary.name)
    )
    if q:
        stmt = stmt.where(AdminBoundary.name.ilike(f"%{q.strip()}%"))
    rows = (await session.execute(stmt)).all()
    return [
        BoundaryRead(
            id=r.id, name=r.name, level=r.level, code=r.code,
            area_km2=round(r.area_km2, 1) if r.area_km2 else None,
        )
        for r in rows
    ]


@router.get("/{boundary_id}/aliases", response_model=list[AliasRead])
async def list_aliases(
    boundary_id: UUID, session: AsyncSession = Depends(get_session)
) -> list[AliasRead]:
    rows = (
        await session.execute(
            select(PlaceAlias).where(PlaceAlias.boundary_id == boundary_id)
            .order_by(PlaceAlias.alias)
        )
    ).scalars().all()
    return [
        AliasRead(id=r.id, alias=r.alias, boundary_id=r.boundary_id, source=r.source)
        for r in rows
    ]


@router.post(
    "/{boundary_id}/aliases",
    response_model=AliasRead,
    status_code=201,
    summary="Teach the geocoder a new place name",
    description=(
        "Adding a place name is a row, not a deploy. `source` records how the "
        "mapping was established so a lookup is never mistaken for a guess."
    ),
)
async def create_alias(
    boundary_id: UUID,
    payload: AliasIn,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> AliasRead:
    if await session.get(AdminBoundary, boundary_id) is None:
        raise HTTPException(status_code=404, detail="Boundary not found")

    normalised = payload.alias.strip().lower()
    if not normalised:
        raise HTTPException(status_code=422, detail="alias cannot be empty")

    existing = (
        await session.execute(select(PlaceAlias).where(PlaceAlias.alias == normalised))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"Alias {normalised!r} already exists"
        )

    alias = PlaceAlias(
        alias=normalised, boundary_id=boundary_id, source=payload.source
    )
    session.add(alias)
    await session.commit()
    return AliasRead(
        id=alias.id, alias=alias.alias, boundary_id=alias.boundary_id,
        source=alias.source,
    )
