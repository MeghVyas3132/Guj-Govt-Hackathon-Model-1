"""Coverage gap analysis over HTTP."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.coverage import CoverageRun
from app.schemas.coverage import CoverageRunRead, CoverageRunRequest
from app.services.coverage import CoverageService, CoverageTooLargeError

router = APIRouter(prefix="/coverage", tags=["coverage"])


@router.post(
    "/runs",
    response_model=CoverageRunRead,
    status_code=201,
    summary="Run a gap analysis over one district or taluka",
    description=(
        "Tessellates the boundary into hexagons and measures each against the union "
        "of camera footprints, twice: over all cameras (installed coverage) and over "
        "online cameras only (effective coverage). The difference is coverage lost to "
        "outages -- cameras that exist but are not watching."
    ),
)
async def create_run(
    request: CoverageRunRequest, session: AsyncSession = Depends(get_session)
) -> CoverageRunRead:
    try:
        run = await CoverageService(session).run(request)
    except CoverageTooLargeError as exc:
        # 422, not 500: the request is answerable, just not at this resolution, and
        # the message names the edge length that would work.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CoverageRunRead.model_validate(run)


@router.get(
    "/estimate",
    summary="Cell count for an AOI without running the analysis",
    description="Lets a client pick a workable resolution before committing to a run.",
)
async def estimate(
    boundary_id: UUID = Query(...),
    hex_edge_m: float = Query(150.0, ge=25, le=5000),
    session: AsyncSession = Depends(get_session),
) -> dict[str, float | int | bool]:
    from app.services.coverage import DEFAULT_MAX_CELLS

    cells = await CoverageService(session).estimate_cells(boundary_id, hex_edge_m)
    return {
        "hex_edge_m": hex_edge_m,
        "estimated_cells": cells,
        "max_cells": DEFAULT_MAX_CELLS,
        "within_budget": cells <= DEFAULT_MAX_CELLS,
    }


@router.get("/runs", response_model=list[CoverageRunRead], summary="Recent runs")
async def list_runs(
    limit: int = Query(20, le=100), session: AsyncSession = Depends(get_session)
) -> list[CoverageRunRead]:
    rows = (
        await session.execute(
            select(CoverageRun).order_by(CoverageRun.created_at.desc()).limit(limit)
        )
    ).scalars().all()
    return [CoverageRunRead.model_validate(r) for r in rows]


@router.get("/runs/{run_id}", response_model=CoverageRunRead)
async def get_run(
    run_id: UUID, session: AsyncSession = Depends(get_session)
) -> CoverageRunRead:
    run = await session.get(CoverageRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Coverage run not found")
    return CoverageRunRead.model_validate(run)


@router.get(
    "/runs/{run_id}/report.html",
    summary="Printable gap-analysis report",
    response_class=Response,
)
async def report(
    run_id: UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    from app.services.report import render_coverage_report

    run = await session.get(CoverageRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Coverage run not found")
    service = CoverageService(session)
    return Response(
        content=render_coverage_report(
            run,
            await service.outage_cells(run_id),
            await service.classification_counts(run_id),
            await service.zero_coverage_cells(run_id),
        ),
        media_type="text/html",
    )


@router.get(
    "/runs/{run_id}/tiles/{z}/{x}/{y}.mvt",
    summary="Coverage cells as a vector tile layer",
    response_class=Response,
)
async def coverage_tile(
    run_id: UUID,
    z: int = Path(ge=0, le=22),
    x: int = Path(ge=0),
    y: int = Path(ge=0),
    session: AsyncSession = Depends(get_session),
) -> Response:
    from app.services.tiles import CoverageTileService

    tile = await CoverageTileService(session).tile(run_id, z, x, y)
    if not tile:
        return Response(status_code=204)
    return Response(
        content=tile,
        media_type="application/vnd.mapbox-vector-tile",
        headers={"Cache-Control": "public, max-age=300"},
    )
