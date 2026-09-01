"""Controlled vocabulary administration.

The UI reads its dropdowns from here rather than shipping hardcoded option lists,
so a term added to the database appears in the interface on the next page load.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import request_context, require_scope
from app.models.vocabulary import DIMENSIONS, VocabularyTerm
from app.schemas.auth import Principal
from app.services.audit import AuditService

router = APIRouter(prefix="/vocabulary", tags=["vocabulary"])


class TermRead(BaseModel):
    id: UUID
    dimension: str
    code: str
    label: str
    description: str | None = None
    is_fallback: bool
    is_active: bool
    sort_order: int
    coverage_range_m: float | None = None
    coverage_fov_deg: float | None = None
    is_omnidirectional: bool | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class TermCreate(BaseModel):
    code: str = Field(max_length=64)
    label: str = Field(max_length=200)
    description: str | None = None
    sort_order: int = 100
    coverage_range_m: float | None = Field(default=None, gt=0)
    coverage_fov_deg: float | None = Field(default=None, gt=0, le=360)
    is_omnidirectional: bool | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class TermUpdate(BaseModel):
    label: str | None = None
    description: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None
    coverage_range_m: float | None = Field(default=None, gt=0)
    coverage_fov_deg: float | None = Field(default=None, gt=0, le=360)
    is_omnidirectional: bool | None = None


@router.get("", summary="Every dimension and how many terms it holds")
async def list_dimensions(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (
        await session.execute(
            select(VocabularyTerm.dimension, VocabularyTerm.is_active)
        )
    ).all()
    counts: dict[str, dict[str, int]] = {
        d: {"total": 0, "active": 0} for d in DIMENSIONS
    }
    for dimension, active in rows:
        bucket = counts.setdefault(dimension, {"total": 0, "active": 0})
        bucket["total"] += 1
        bucket["active"] += int(active)
    return [{"dimension": d, **counts[d]} for d in sorted(counts)]


@router.get(
    "/{dimension}",
    response_model=list[TermRead],
    summary="Terms in one dimension",
    description="Active terms by default; the UI populates its dropdowns from this.",
)
async def list_terms(
    dimension: str = Path(...),
    include_inactive: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[TermRead]:
    stmt = select(VocabularyTerm).where(VocabularyTerm.dimension == dimension)
    if not include_inactive:
        stmt = stmt.where(VocabularyTerm.is_active)
    rows = (
        await session.execute(stmt.order_by(VocabularyTerm.sort_order, VocabularyTerm.code))
    ).scalars().all()
    return [TermRead.model_validate(r, from_attributes=True) for r in rows]


@router.post(
    "/{dimension}",
    response_model=TermRead,
    status_code=201,
    summary="Add a term",
    description=(
        "Adding a camera type, status or site type is a row. For camera_type, the "
        "coverage fields feed the gap analysis directly, so a newly-added type is "
        "modelled correctly without a code change."
    ),
)
async def create_term(
    payload: TermCreate,
    dimension: str = Path(...),
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
    context: dict = Depends(request_context),
) -> TermRead:
    if dimension not in DIMENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown dimension {dimension!r}. Known: {sorted(DIMENSIONS)}",
        )

    code = payload.code.strip().lower()
    existing = (
        await session.execute(
            select(VocabularyTerm).where(
                VocabularyTerm.dimension == dimension, VocabularyTerm.code == code
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"{dimension}:{code} already exists"
        )

    term = VocabularyTerm(
        dimension=dimension, code=code, **payload.model_dump(exclude={"code"})
    )
    session.add(term)
    await session.flush()
    AuditService(session).record(
        action="vocabulary.term_added", entity_type="vocabulary_term",
        entity_id=term.id, actor=principal,
        after={"dimension": dimension, "code": code, "label": term.label}, **context,
    )
    await session.commit()
    return TermRead.model_validate(term, from_attributes=True)


@router.patch("/{dimension}/{code}", response_model=TermRead)
async def update_term(
    payload: TermUpdate,
    dimension: str = Path(...),
    code: str = Path(...),
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
    context: dict = Depends(request_context),
) -> TermRead:
    term = (
        await session.execute(
            select(VocabularyTerm).where(
                VocabularyTerm.dimension == dimension, VocabularyTerm.code == code
            )
        )
    ).scalar_one_or_none()
    if term is None:
        raise HTTPException(status_code=404, detail=f"{dimension}:{code} not found")

    before = {"label": term.label, "is_active": term.is_active,
              "coverage_range_m": term.coverage_range_m}
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(term, field, value)

    if term.is_fallback and payload.is_active is False:
        # Deactivating the fallback leaves unknown values with nowhere to go.
        raise HTTPException(
            status_code=422,
            detail=(
                f"{dimension}:{code} is the fallback term for its dimension. "
                "Unrecognised values normalise to it, so it cannot be deactivated."
            ),
        )

    AuditService(session).record(
        action="vocabulary.term_updated", entity_type="vocabulary_term",
        entity_id=term.id, actor=principal, before=before,
        after={"label": term.label, "is_active": term.is_active,
               "coverage_range_m": term.coverage_range_m}, **context,
    )
    await session.commit()
    return TermRead.model_validate(term, from_attributes=True)
