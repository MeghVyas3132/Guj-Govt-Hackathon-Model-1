from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.camera import Camera
from app.models.camera_health import CameraHealth
from app.schemas.health import HealthObservationIn


@dataclass
class RecordOutcome:
    changed: bool
    previous_status: str
    new_status: str


class HealthService:
    """Writes the observation log and projects the current state onto the camera row.

    status_since marks when the camera ENTERED its current state, so a camera observed
    offline every five minutes since 10:00 still reports 10:00 -- which is what the
    downtime column on the dashboard depends on.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self, camera: Camera, observation: HealthObservationIn, source: str = "probe"
    ) -> RecordOutcome:
        observed_at = observation.observed_at or datetime.now(UTC)
        status = observation.status.value

        self.session.add(
            CameraHealth(
                camera_id=camera.id,
                status=status,
                observed_at=observed_at,
                source=source,
                latency_ms=observation.latency_ms,
                detail=observation.detail,
            )
        )

        previous = camera.current_status
        changed = previous != status

        if changed:
            camera.current_status = status
            camera.status_since = observed_at

        # Guard against a delayed or replayed observation moving the clock backwards.
        if camera.last_seen_at is None or observed_at > camera.last_seen_at:
            camera.last_seen_at = observed_at

        await self.session.flush()
        return RecordOutcome(changed=changed, previous_status=previous, new_status=status)
