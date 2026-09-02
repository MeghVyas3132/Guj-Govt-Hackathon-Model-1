from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import CameraStatus
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
        self,
        camera: Camera,
        observation: HealthObservationIn,
        source: str = "probe",
        notify: bool = True,
    ) -> RecordOutcome:
        """Record one observation.

        `notify` exists so a bulk backfill can load history without firing an
        alert per row for events that are months old.
        """
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

        # Only on a transition. Emitting per observation would send an alert every
        # five minutes for as long as a camera stays down, which trains operators
        # to ignore the channel.
        if changed and notify:
            await self._notify(camera, previous, status, observed_at)

        return RecordOutcome(changed=changed, previous_status=previous, new_status=status)

    async def _notify(
        self, camera: Camera, previous: str | None, status: str, observed_at: datetime
    ) -> None:
        """Announce a status transition to subscribers.

        Never raises: the observation is already recorded, and a dead subscriber
        endpoint must not roll back real health data.
        """
        from app.services.webhooks import (
            CAMERA_OFFLINE,
            CAMERA_ONLINE,
            CAMERA_STATUS_CHANGED,
            WebhookService,
        )

        data = {
            "camera_id": str(camera.id),
            "camera_uid": camera.camera_uid,
            "external_camera_id": camera.external_camera_id,
            "name": camera.name,
            "department_id": str(camera.department_id),
            "previous_status": previous,
            "status": status,
            "observed_at": observed_at.isoformat(),
        }

        service = WebhookService(self.session)
        # The specific event as well as the general one, so a subscriber can ask
        # for outages alone without filtering every transition client-side.
        events = [CAMERA_STATUS_CHANGED]
        if status == CameraStatus.OFFLINE.value:
            events.append(CAMERA_OFFLINE)
        elif status == CameraStatus.ONLINE.value:
            events.append(CAMERA_ONLINE)

        for event in events:
            try:
                await service.emit(event, data, department_id=camera.department_id)
            except Exception:  # noqa: BLE001 - see docstring
                continue
