from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_boundary import AdminBoundary
from app.models.camera import Camera
from app.schemas.filters import CameraFilter


def _origin(filters: CameraFilter) -> Any:
    """The radius-search centre, as a geography.

    Casting to `Camera.location.type` rather than leaving it a geometry is what makes
    ST_DWithin and ST_Distance measure in metres on the spheroid. Left as geometry
    they would measure in *degrees*, and a 2000 "metre" radius would silently become
    a 2000-degree one that spans the planet.
    """
    return func.ST_SetSRID(
        func.ST_MakePoint(filters.near_lon, filters.near_lat), 4326
    ).cast(Camera.location.type)


class CameraRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_external_id(
        self, department_id: UUID, external_camera_id: str
    ) -> Camera | None:
        stmt = select(Camera).where(
            Camera.department_id == department_id,
            Camera.external_camera_id == external_camera_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def next_uid(self, department_code: str) -> str:
        stmt = (
            select(func.count())
            .select_from(Camera)
            .where(Camera.camera_uid.like(f"GJ-{department_code}-%"))
        )
        count = (await self.session.execute(stmt)).scalar_one()
        return f"GJ-{department_code}-{count + 1:06d}"

    def add(self, camera: Camera) -> None:
        self.session.add(camera)

    def _apply(self, stmt: Select, filters: CameraFilter) -> Select:
        """Narrow a statement by every set field on the filter.

        Shared by `list`, `count` and (via the same CameraFilter) the CSV export, so
        all three agree by construction rather than by three parallel edits.
        """
        stmt = stmt.where(Camera.is_active, Camera.lifecycle_state == "active")

        if filters.department_ids:
            stmt = stmt.where(Camera.department_id.in_(filters.department_ids))
        if filters.camera_types:
            stmt = stmt.where(Camera.camera_type.in_([t.value for t in filters.camera_types]))
        if filters.statuses:
            stmt = stmt.where(Camera.current_status.in_([s.value for s in filters.statuses]))
        if filters.ownership_classes:
            stmt = stmt.where(
                Camera.ownership_class.in_([o.value for o in filters.ownership_classes])
            )
        if filters.q:
            pattern = f"%{filters.q.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(Camera.camera_uid).like(pattern),
                    func.lower(Camera.name).like(pattern),
                    func.lower(Camera.address).like(pattern),
                    func.lower(Camera.external_camera_id).like(pattern),
                )
            )
        if filters.district_id:
            stmt = stmt.where(
                Camera.location.ST_Intersects(
                    select(AdminBoundary.geom)
                    .where(AdminBoundary.id == filters.district_id)
                    .scalar_subquery()
                )
            )
        if filters.has_radius:
            stmt = stmt.where(
                func.ST_DWithin(Camera.location, _origin(filters), filters.radius_m)
            )
        return stmt

    async def list(
        self, filters: CameraFilter, limit: int = 50, offset: int = 0
    ) -> list[Camera]:
        stmt = self._apply(select(Camera), filters)
        if filters.has_radius:
            # Nearest first -- exactly the question an investigator asks at an incident
            # location: "what could have seen this, closest camera first?"
            stmt = stmt.order_by(func.ST_Distance(Camera.location, _origin(filters)))
        else:
            stmt = stmt.order_by(Camera.camera_uid)
        result = await self.session.execute(stmt.limit(limit).offset(offset))
        return list(result.scalars().all())

    async def count(self, filters: CameraFilter) -> int:
        """Total matches ignoring pagination, for the Page envelope."""
        stmt = self._apply(select(func.count()).select_from(Camera), filters)
        return (await self.session.execute(stmt)).scalar_one()

    async def list_nearby(
        self, filters: CameraFilter, limit: int = 50
    ) -> list[tuple[Camera, float]]:
        """Matching cameras paired with their true distance from the origin, in metres.

        The same `_apply` and the same `_origin` as `list`, so a camera the table
        shows inside the radius can never be missing from the nearest-first answer.
        Returned as rows rather than scalars because the distance is computed by
        PostGIS and has nowhere to live on the ORM object.
        """
        distance = func.ST_Distance(Camera.location, _origin(filters)).label("distance_m")
        stmt = self._apply(select(Camera, distance), filters).order_by(distance).limit(limit)
        rows = (await self.session.execute(stmt)).all()
        return [(row[0], float(row[1])) for row in rows]
