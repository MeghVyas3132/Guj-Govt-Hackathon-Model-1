"""Source connector administration.

Onboarding a new department's camera system is a POST here plus a field-mapping
config. No code, no deploy, no vendor name anywhere in the application.
"""

from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.rest_catalogue import RestCatalogueAdapter
from app.core.db import get_session
from app.models.department import Department
from app.models.source_connector import Credential, SourceConnector
from app.schemas.connector import ConnectorConfig
from app.schemas.ingestion import IngestReport
from app.services.credentials import CredentialResolver
from app.services.ingestion import IngestionService

router = APIRouter(prefix="/connectors", tags=["connectors"])


class ConnectorIn(BaseModel):
    code: str
    name: str
    department_id: UUID
    config: ConnectorConfig


class ConnectorOut(BaseModel):
    id: UUID
    code: str
    name: str
    department_id: UUID
    config: dict
    is_active: bool


class CredentialIn(BaseModel):
    name: str
    value: str
    description: str | None = None


async def _load(session: AsyncSession, code: str) -> SourceConnector:
    connector = (
        await session.execute(select(SourceConnector).where(SourceConnector.code == code))
    ).scalar_one_or_none()
    if connector is None or not connector.is_active:
        raise HTTPException(status_code=404, detail=f"No active connector {code!r}")
    return connector


@router.post("", response_model=ConnectorOut, status_code=201)
async def create_connector(
    payload: ConnectorIn, session: AsyncSession = Depends(get_session)
) -> ConnectorOut:
    if await session.get(Department, payload.department_id) is None:
        raise HTTPException(status_code=404, detail="Department not found")
    existing = (
        await session.execute(
            select(SourceConnector).where(SourceConnector.code == payload.code)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Connector {payload.code!r} exists")

    connector = SourceConnector(
        code=payload.code,
        name=payload.name,
        department_id=payload.department_id,
        # Round-tripped through ConnectorConfig, so a malformed connector is
        # rejected at write time rather than discovered mid-sync.
        config=payload.config.model_dump(mode="json"),
    )
    session.add(connector)
    await session.commit()
    return ConnectorOut(
        id=connector.id, code=connector.code, name=connector.name,
        department_id=connector.department_id, config=connector.config,
        is_active=connector.is_active,
    )


@router.get("", response_model=list[ConnectorOut])
async def list_connectors(
    session: AsyncSession = Depends(get_session),
) -> list[ConnectorOut]:
    rows = (
        await session.execute(select(SourceConnector).order_by(SourceConnector.code))
    ).scalars().all()
    return [
        ConnectorOut(
            id=r.id, code=r.code, name=r.name, department_id=r.department_id,
            config=r.config, is_active=r.is_active,
        )
        for r in rows
    ]


@router.post(
    "/{code}/sync",
    response_model=IngestReport,
    summary="Pull a source catalogue and onboard it",
    description=(
        "Reads the connector's catalogue and runs every entry through the same "
        "validation and normalization as a CSV upload. Idempotent: re-running "
        "produces no changes when nothing upstream has changed."
    ),
)
async def sync_connector(
    code: str,
    limit: int | None = Query(None, ge=1, description="Cap for a smoke test."),
    session: AsyncSession = Depends(get_session),
) -> IngestReport:
    connector = await _load(session, code)
    department = await session.get(Department, connector.department_id)
    if department is None:
        raise HTTPException(status_code=404, detail="Connector's department is missing")

    config = ConnectorConfig.model_validate(connector.config)
    secret = await CredentialResolver(session).resolve(config.auth.credential_ref)
    if config.auth.type != "none" and secret is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Connector {code!r} needs credential {config.auth.credential_ref!r}, "
                "which is not set. Add it to credentials or the environment."
            ),
        )

    adapter = RestCatalogueAdapter(config, secret=secret, code=code)
    try:
        records = await adapter.fetch(connector.department_id)
    except httpx.HTTPError as exc:
        # 502 rather than 500: on demo day this says the upstream is unreachable,
        # not that our code is broken.
        raise HTTPException(
            status_code=502, detail=f"Could not reach the source catalogue: {exc}"
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=502, detail=f"Source catalogue was not understood: {exc}"
        ) from exc

    if limit is not None:
        records = records[:limit]
    return await IngestionService(session).ingest(records, department, mode="commit")


@router.post("/credentials", status_code=201)
async def upsert_credential(
    payload: CredentialIn, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    existing = (
        await session.execute(select(Credential).where(Credential.name == payload.name))
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            Credential(
                name=payload.name, value=payload.value, description=payload.description
            )
        )
    else:
        existing.value = payload.value
        existing.description = payload.description
    await session.commit()
    # Never echoes the value back.
    return {"name": payload.name, "status": "stored"}
