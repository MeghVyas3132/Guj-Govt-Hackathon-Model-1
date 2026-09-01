"""Department registration and its versioned field-mapping config.

Onboarding a department is a row here, not a code change: the mapping config is what
translates that department's column names into the canonical ones, so a new source is
a PUT rather than a deploy.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import require_scope
from app.models.department import Department
from app.models.field_mapping import FieldMapping
from app.schemas.auth import Principal

router = APIRouter(prefix="/departments", tags=["departments"])


class DepartmentCreate(BaseModel):
    code: str = Field(max_length=16, examples=["RTO"])
    name: str = Field(max_length=200, examples=["Regional Transport Office"])
    dept_type: str = "government"
    contact_email: str | None = None


class DepartmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    dept_type: str
    is_active: bool


class FieldMappingWrite(BaseModel):
    config: dict[str, Any] = Field(
        examples=[
            {
                "column_map": {"cam_id": "external_camera_id", "lat": "latitude"},
                "value_maps": {"status": {"RUNNING": "online", "HALTED": "offline"}},
            }
        ]
    )


class FieldMappingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    department_id: UUID
    version: int
    config: dict[str, Any]
    is_active: bool


@router.post(
    "",
    response_model=DepartmentRead,
    status_code=201,
    summary="Register a department",
)
async def create_department(
    payload: DepartmentCreate,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> DepartmentRead:
    # `code` is the camera_uid prefix (GJ-AMC-000001), so a duplicate is a 409 rather
    # than a database error surfacing as a 500: two departments sharing a code would
    # make the uid ambiguous.
    existing = (
        await session.execute(select(Department).where(Department.code == payload.code))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"Department code {payload.code} already exists."
        )

    department = Department(**payload.model_dump())
    session.add(department)
    await session.commit()
    return DepartmentRead.model_validate(department)


@router.get("", response_model=list[DepartmentRead], summary="List departments")
async def list_departments(
    session: AsyncSession = Depends(get_session),
) -> list[DepartmentRead]:
    rows = (
        (await session.execute(select(Department).order_by(Department.code)))
        .scalars()
        .all()
    )
    return [DepartmentRead.model_validate(row) for row in rows]


@router.get(
    "/{department_id}/field-mappings",
    response_model=FieldMappingRead,
    summary="The department's current field mapping",
    description="Returns the highest version, which is the one a new import will use.",
)
async def get_field_mapping(
    department_id: UUID, session: AsyncSession = Depends(get_session)
) -> FieldMappingRead:
    mapping = (
        (
            await session.execute(
                select(FieldMapping)
                .where(FieldMapping.department_id == department_id)
                .order_by(FieldMapping.version.desc())
            )
        )
        .scalars()
        .first()
    )
    if mapping is None:
        raise HTTPException(status_code=404, detail="No field mapping configured.")
    return FieldMappingRead.model_validate(mapping)


@router.put(
    "/{department_id}/field-mappings",
    response_model=FieldMappingRead,
    summary="Publish a new field-mapping version",
    description=(
        "Creates version N+1 rather than overwriting version N. Each camera records "
        "the mapping version it was translated under, so a past import stays "
        "reproducible after the config changes."
    ),
)
async def put_field_mapping(
    department_id: UUID,
    payload: FieldMappingWrite,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> FieldMappingRead:
    if await session.get(Department, department_id) is None:
        raise HTTPException(status_code=404, detail="Department not found")

    # New version rather than overwrite. `cameras.field_mapping_version` points back
    # at the config a row was translated under; overwriting would silently rewrite
    # the history those pointers describe.
    current_max = (
        await session.execute(
            select(func.coalesce(func.max(FieldMapping.version), 0)).where(
                FieldMapping.department_id == department_id
            )
        )
    ).scalar_one()

    mapping = FieldMapping(
        department_id=department_id, version=current_max + 1, config=payload.config
    )
    session.add(mapping)
    await session.commit()
    return FieldMappingRead.model_validate(mapping)
