"""Ageing infrastructure.

The registry knows when each camera was installed, when its maintenance contract
expires and how long it retains footage. Those three facts answer the question a
procurement officer actually has -- what do I need to replace, and when -- which
is the second half of the gap analysis the problem statement asks for. Uncovered
zones are a map question; ageing is a calendar question, and they need different
reports.

Every threshold is a parameter rather than a constant. Replacement cycles differ
by department and by procurement round, and nothing here should encode one
office's policy as the system's opinion.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Date, Integer, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.camera import Camera
from app.models.department import Department

# Defaults, not rules. A five-year service life is the common assumption for
# fixed CCTV in Indian municipal procurement; the caller overrides it freely.
DEFAULT_SERVICE_LIFE_YEARS = 5
DEFAULT_AMC_HORIZON_DAYS = 90
DEFAULT_MIN_RETENTION_DAYS = 30


@dataclass
class AgeingBand:
    label: str
    count: int
    share: float


@dataclass
class DepartmentAgeing:
    department_id: str
    department_code: str
    department_name: str
    total: int
    past_service_life: int
    amc_expired: int
    amc_expiring_soon: int
    retention_below_policy: int
    unknown_install_date: int
    oldest_install_date: date | None


@dataclass
class AgeingReport:
    generated_for: date
    service_life_years: int
    amc_horizon_days: int
    min_retention_days: int
    total_cameras: int
    past_service_life: int
    amc_expired: int
    amc_expiring_soon: int
    retention_below_policy: int
    unknown_install_date: int
    # Cameras in at least one problem category. Deliberately not the sum of the
    # categories: one camera can be past its service life *and* out of AMC, and
    # adding those columns inflates the number an officer takes to a budget
    # meeting. Counted in SQL, so it is a real distinct count.
    needs_attention: int = 0
    bands: list[AgeingBand] = field(default_factory=list)
    departments: list[DepartmentAgeing] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_for": self.generated_for.isoformat(),
            "thresholds": {
                "service_life_years": self.service_life_years,
                "amc_horizon_days": self.amc_horizon_days,
                "min_retention_days": self.min_retention_days,
            },
            "totals": {
                "cameras": self.total_cameras,
                "needs_attention": self.needs_attention,
                "past_service_life": self.past_service_life,
                "amc_expired": self.amc_expired,
                "amc_expiring_soon": self.amc_expiring_soon,
                "retention_below_policy": self.retention_below_policy,
                "unknown_install_date": self.unknown_install_date,
            },
            "bands": [
                {"label": b.label, "count": b.count, "share": b.share} for b in self.bands
            ],
            "departments": [
                {
                    "department_id": d.department_id,
                    "department_code": d.department_code,
                    "department_name": d.department_name,
                    "total": d.total,
                    "past_service_life": d.past_service_life,
                    "amc_expired": d.amc_expired,
                    "amc_expiring_soon": d.amc_expiring_soon,
                    "retention_below_policy": d.retention_below_policy,
                    "unknown_install_date": d.unknown_install_date,
                    "oldest_install_date": (
                        d.oldest_install_date.isoformat() if d.oldest_install_date else None
                    ),
                }
                for d in self.departments
            ],
        }


# Age bands in years. Open-ended at the top so nothing falls outside.
_BANDS: list[tuple[str, int | None, int | None]] = [
    ("Under 1 year", None, 1),
    ("1-3 years", 1, 3),
    ("3-5 years", 3, 5),
    ("5-8 years", 5, 8),
    ("Over 8 years", 8, None),
]


class LifecycleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ageing(
        self,
        *,
        as_of: date | None = None,
        service_life_years: int = DEFAULT_SERVICE_LIFE_YEARS,
        amc_horizon_days: int = DEFAULT_AMC_HORIZON_DAYS,
        min_retention_days: int = DEFAULT_MIN_RETENTION_DAYS,
        department_id: str | None = None,
    ) -> AgeingReport:
        as_of = as_of or date.today()
        # timedelta rather than relativedelta: a service life is a procurement
        # convention, not a calendar anniversary, and 365.25 days keeps leap years
        # from shifting the boundary by a day between runs.
        service_life_cutoff = as_of - timedelta(days=int(service_life_years * 365.25))
        amc_horizon = as_of + timedelta(days=amc_horizon_days)

        install = Camera.install_date
        amc = Camera.amc_expiry_date
        retention = Camera.retention_days

        past_life = case((install.isnot(None) & (install <= service_life_cutoff), 1), else_=0)
        amc_expired = case((amc.isnot(None) & (amc < as_of), 1), else_=0)
        amc_soon = case(
            (amc.isnot(None) & (amc >= as_of) & (amc <= amc_horizon), 1), else_=0
        )
        low_retention = case(
            (retention.isnot(None) & (retention < min_retention_days), 1), else_=0
        )
        unknown_install = case((install.is_(None), 1), else_=0)
        # A camera counts once however many ways it is in trouble.
        attention = case(
            (
                (past_life == 1) | (amc_expired == 1) | (amc_soon == 1) | (low_retention == 1),
                1,
            ),
            else_=0,
        )

        base = select(Camera).where(Camera.is_active)
        if department_id:
            base = base.where(Camera.department_id == department_id)
        scope = base.subquery()

        totals_stmt = select(
            func.count(),
            func.coalesce(func.sum(past_life), 0),
            func.coalesce(func.sum(amc_expired), 0),
            func.coalesce(func.sum(amc_soon), 0),
            func.coalesce(func.sum(low_retention), 0),
            func.coalesce(func.sum(unknown_install), 0),
            func.coalesce(func.sum(attention), 0),
        ).select_from(Camera).where(Camera.is_active)
        if department_id:
            totals_stmt = totals_stmt.where(Camera.department_id == department_id)

        row = (await self.session.execute(totals_stmt)).one()
        total, past, expired, soon, low, unknown, attention_count = (int(v) for v in row)

        report = AgeingReport(
            generated_for=as_of,
            service_life_years=service_life_years,
            amc_horizon_days=amc_horizon_days,
            min_retention_days=min_retention_days,
            total_cameras=total,
            past_service_life=past,
            amc_expired=expired,
            amc_expiring_soon=soon,
            retention_below_policy=low,
            unknown_install_date=unknown,
            needs_attention=attention_count,
        )
        report.bands = await self._bands(as_of, total, department_id)
        report.departments = await self._by_department(
            as_of, service_life_cutoff, amc_horizon, min_retention_days, department_id
        )
        return report

    async def _bands(
        self, as_of: date, total: int, department_id: str | None
    ) -> list[AgeingBand]:
        bands: list[AgeingBand] = []
        for label, lower, upper in _BANDS:
            stmt = select(func.count()).select_from(Camera).where(
                Camera.is_active, Camera.install_date.isnot(None)
            )
            if department_id:
                stmt = stmt.where(Camera.department_id == department_id)
            # Older than N years means installed before the cutoff for N.
            if lower is not None:
                stmt = stmt.where(
                    Camera.install_date <= as_of - timedelta(days=int(lower * 365.25))
                )
            if upper is not None:
                stmt = stmt.where(
                    Camera.install_date > as_of - timedelta(days=int(upper * 365.25))
                )
            count = int((await self.session.execute(stmt)).scalar_one())
            bands.append(
                AgeingBand(label, count, round(count / total * 100, 2) if total else 0.0)
            )
        return bands

    async def _by_department(
        self,
        as_of: date,
        service_life_cutoff: date,
        amc_horizon: date,
        min_retention_days: int,
        department_id: str | None,
    ) -> list[DepartmentAgeing]:
        install = Camera.install_date
        amc = Camera.amc_expiry_date

        stmt = (
            select(
                Department.id,
                Department.code,
                Department.name,
                func.count(Camera.id),
                func.coalesce(
                    func.sum(case((install.isnot(None) & (install <= service_life_cutoff), 1), else_=0)), 0
                ),
                func.coalesce(func.sum(case((amc.isnot(None) & (amc < as_of), 1), else_=0)), 0),
                func.coalesce(
                    func.sum(case((amc.isnot(None) & (amc >= as_of) & (amc <= amc_horizon), 1), else_=0)), 0
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                Camera.retention_days.isnot(None)
                                & (Camera.retention_days < min_retention_days),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(func.sum(case((install.is_(None), 1), else_=0)), 0),
                func.min(install),
            )
            .join(Camera, Camera.department_id == Department.id)
            .where(Camera.is_active)
            .group_by(Department.id, Department.code, Department.name)
            # Worst first: this is a worklist, not a directory.
            .order_by(
                func.coalesce(
                    func.sum(case((install.isnot(None) & (install <= service_life_cutoff), 1), else_=0)), 0
                ).desc(),
                Department.code,
            )
        )
        if department_id:
            stmt = stmt.where(Camera.department_id == department_id)

        return [
            DepartmentAgeing(
                department_id=str(r[0]),
                department_code=r[1],
                department_name=r[2],
                total=int(r[3]),
                past_service_life=int(r[4]),
                amc_expired=int(r[5]),
                amc_expiring_soon=int(r[6]),
                retention_below_policy=int(r[7]),
                unknown_install_date=int(r[8]),
                oldest_install_date=r[9],
            )
            for r in (await self.session.execute(stmt)).all()
        ]


__all__ = ["AgeingReport", "LifecycleService"]
