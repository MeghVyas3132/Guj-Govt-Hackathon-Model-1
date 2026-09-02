"""Ageing-infrastructure reporting.

The problem statement asks for gap analysis over "uncovered zones and ageing
infrastructure". Coverage answers the first; this answers the second.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import require_scope
from app.schemas.auth import Principal
from app.services.lifecycle import (
    DEFAULT_AMC_HORIZON_DAYS,
    DEFAULT_MIN_RETENTION_DAYS,
    DEFAULT_SERVICE_LIFE_YEARS,
    LifecycleService,
)

router = APIRouter(prefix="/lifecycle", tags=["lifecycle"])


@router.get(
    "/ageing",
    summary="Ageing-infrastructure report",
    description=(
        "Cameras past their service life, out of or nearing AMC expiry, and "
        "retaining less footage than policy requires. Every threshold is a "
        "parameter: replacement cycles differ by department and procurement round."
    ),
)
async def ageing(
    as_of: date | None = Query(
        None, description="Defaults to today. Set it to reproduce an earlier report."
    ),
    service_life_years: int = Query(DEFAULT_SERVICE_LIFE_YEARS, ge=1, le=30),
    amc_horizon_days: int = Query(DEFAULT_AMC_HORIZON_DAYS, ge=0, le=1095),
    min_retention_days: int = Query(DEFAULT_MIN_RETENTION_DAYS, ge=0, le=3650),
    department_id: str | None = Query(None),
    principal: Principal = Depends(require_scope("cameras:read")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    report = await LifecycleService(session).ageing(
        as_of=as_of,
        service_life_years=service_life_years,
        amc_horizon_days=amc_horizon_days,
        min_retention_days=min_retention_days,
        department_id=department_id,
    )
    return report.to_dict()


@router.get(
    "/ageing.csv",
    summary="Ageing report as CSV",
    description="The per-department table, for a procurement spreadsheet.",
    response_class=Response,
)
async def ageing_csv(
    as_of: date | None = Query(None),
    service_life_years: int = Query(DEFAULT_SERVICE_LIFE_YEARS, ge=1, le=30),
    amc_horizon_days: int = Query(DEFAULT_AMC_HORIZON_DAYS, ge=0, le=1095),
    min_retention_days: int = Query(DEFAULT_MIN_RETENTION_DAYS, ge=0, le=3650),
    department_id: str | None = Query(None),
    principal: Principal = Depends(require_scope("cameras:export")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    import csv
    import io

    report = await LifecycleService(session).ageing(
        as_of=as_of,
        service_life_years=service_life_years,
        amc_horizon_days=amc_horizon_days,
        min_retention_days=min_retention_days,
        department_id=department_id,
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "department_code", "department_name", "cameras", "past_service_life",
        "amc_expired", "amc_expiring_soon", "retention_below_policy",
        "unknown_install_date", "oldest_install_date",
    ])
    for d in report.departments:
        writer.writerow([
            d.department_code, d.department_name, d.total, d.past_service_life,
            d.amc_expired, d.amc_expiring_soon, d.retention_below_policy,
            d.unknown_install_date,
            d.oldest_install_date.isoformat() if d.oldest_install_date else "",
        ])

    return Response(
        # utf-8-sig so Excel opens Gujarati department names correctly rather
        # than as mojibake -- the same reason the CSV reader strips a BOM.
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="ageing-{report.generated_for.isoformat()}.csv"'
            )
        },
    )
