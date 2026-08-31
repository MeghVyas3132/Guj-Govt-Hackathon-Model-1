from datetime import UTC, datetime, timedelta

from geoalchemy2 import Geometry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.camera import Camera
from app.models.department import Department
from app.schemas.health import HealthSummary, OfflineCamera


class HealthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def offline(self, limit: int = 200, offset: int = 0) -> list[OfflineCamera]:
        """Currently offline cameras, longest down first.

        `ORDER BY status_since ASC` is the whole trick: because status_since only moves
        on a state *change*, the oldest transition is the longest outage. The
        ix_cameras_status_since index on (current_status, status_since) from Plan 1
        serves the equality and the ordering together, so this stays an index scan
        rather than a sort of the whole table.

        Latitude and longitude are read out by PostGIS rather than by unpacking the
        ORM attribute: `location` is a GEOGRAPHY, and an instance still live in the
        session's identity map holds whatever Python value was assigned to it (a WKT
        string on a freshly inserted row) rather than the WKBElement a fresh load
        returns. Projecting the coordinates in SQL makes the answer depend only on
        what the database holds.
        """
        latitude = func.ST_Y(Camera.location.cast(Geometry)).label("latitude")
        longitude = func.ST_X(Camera.location.cast(Geometry)).label("longitude")
        stmt = (
            select(
                Camera.id,
                Camera.camera_uid,
                Camera.name,
                Camera.status_since,
                Department.code,
                latitude,
                longitude,
            )
            .join(Department, Department.id == Camera.department_id)
            .where(
                Camera.current_status == "offline",
                Camera.is_active,
                Camera.lifecycle_state == "active",
            )
            .order_by(Camera.status_since.asc().nulls_last())
            .limit(limit)
            .offset(offset)
        )
        now = datetime.now(UTC)
        return [
            OfflineCamera(
                camera_id=row.id,
                camera_uid=row.camera_uid,
                name=row.name,
                department_code=row.code,
                latitude=row.latitude,
                longitude=row.longitude,
                status_since=row.status_since,
                downtime_seconds=(
                    (now - row.status_since).total_seconds() if row.status_since else 0.0
                ),
            )
            for row in (await self.session.execute(stmt)).all()
        ]

    async def summary(self) -> HealthSummary:
        now = datetime.now(UTC)
        base = (Camera.is_active, Camera.lifecycle_state == "active")

        stmt = (
            select(Camera.current_status, func.count())
            .where(*base)
            .group_by(Camera.current_status)
        )
        counts = dict((await self.session.execute(stmt)).all())

        async def offline_over(delta: timedelta) -> int:
            stmt = (
                select(func.count())
                .select_from(Camera)
                .where(
                    *base,
                    Camera.current_status == "offline",
                    Camera.status_since < now - delta,
                )
            )
            return (await self.session.execute(stmt)).scalar_one()

        return HealthSummary(
            total=sum(counts.values()),
            online=counts.get("online", 0),
            offline=counts.get("offline", 0),
            unknown=counts.get("unknown", 0),
            maintenance=counts.get("maintenance", 0),
            offline_over_24h=await offline_over(timedelta(hours=24)),
            offline_over_7d=await offline_over(timedelta(days=7)),
        )
