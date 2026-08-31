from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.camera import Camera


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
