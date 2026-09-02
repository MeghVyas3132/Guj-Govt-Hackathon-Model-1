"""Persisting derived stream metadata onto the registry.

Separate from `enrichment` on purpose: that module knows about HLS and nothing
about the database, which is what makes it testable without either. This one
owns the transaction, the credential lookup and the audit entry.
"""

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import StreamProtocol
from app.models.camera import Camera
from app.models.source_connector import SourceConnector
from app.models.stream_endpoint import StreamEndpoint
from app.schemas.auth import Principal
from app.services.audit import AuditService
from app.services.credentials import CredentialResolver
from app.services.enrichment import StreamEnricher, StreamMetadata

# Only these can be read without a media server negotiating a session first.
ENRICHABLE = (StreamProtocol.HLS.value,)

# Lower than the health probe's bound because this is a different kind of work:
# a probe fetches a few KB, whereas enrichment decodes encrypted media. Ten at
# once starves each one until they all hit their timeout.
CONCURRENCY = 4


@dataclass
class EnrichmentOutcome:
    camera_id: UUID
    external_camera_id: str | None
    updated: bool
    metadata: dict[str, Any]
    error: str | None = None


class MetadataService:
    def __init__(
        self, session: AsyncSession, enricher: StreamEnricher | None = None
    ) -> None:
        self.session = session
        self.enricher = enricher or StreamEnricher()
        self.audit = AuditService(session)
        self.credentials = CredentialResolver(session)

    async def _auth_for(
        self, endpoint: StreamEndpoint, connector: SourceConnector | None
    ) -> tuple[str | None, str | None, str | None]:
        """(secret, cookie_name, header_name) for one endpoint.

        The scheme lives on the connector that owns the department, so a fleet
        spanning several vendors enriches correctly rather than assuming one cookie.
        """
        if not (endpoint.requires_auth and endpoint.credential_ref):
            return None, None, None
        secret = await self.credentials.resolve(endpoint.credential_ref)
        auth = (connector.config or {}).get("auth", {}) if connector else {}
        if auth.get("type") == "cookie":
            return secret, auth.get("name"), None
        if auth.get("type") == "header":
            return secret, None, auth.get("name")
        return secret, None, None

    async def enrich(
        self,
        cameras: list[Camera],
        actor: Principal | None = None,
        only_missing: bool = False,
    ) -> list[EnrichmentOutcome]:
        """Derive metadata for these cameras.

        `only_missing` skips any camera whose endpoint already carries a codec
        and resolution. That is what lets a scheduled job converge: the gateway
        is the bottleneck, so a fleet run that re-probes cameras it already
        described spends its whole budget re-learning the same facts and never
        reaches the ones it has not seen.
        """
        if not cameras:
            return []

        ids = [c.id for c in cameras]
        endpoints = (
            await self.session.execute(
                select(StreamEndpoint)
                .where(
                    StreamEndpoint.camera_id.in_(ids),
                    StreamEndpoint.protocol.in_(ENRICHABLE),
                )
                # Primary first, so a camera with several HLS URLs is described by
                # the one an operator would actually open.
                .order_by(StreamEndpoint.is_primary.desc())
            )
        ).scalars().all()

        by_camera: dict[UUID, StreamEndpoint] = {}
        for endpoint in endpoints:
            by_camera.setdefault(endpoint.camera_id, endpoint)

        skipped: list[EnrichmentOutcome] = []
        if only_missing:
            pending = []
            for camera in cameras:
                endpoint = by_camera.get(camera.id)
                if endpoint is not None and endpoint.codec and endpoint.resolution:
                    skipped.append(
                        EnrichmentOutcome(
                            camera.id, camera.external_camera_id, False,
                            {"codec": endpoint.codec, "resolution": endpoint.resolution},
                        )
                    )
                else:
                    pending.append(camera)
            cameras = pending
            if not cameras:
                return skipped

        connectors = (
            await self.session.execute(
                select(SourceConnector).where(
                    SourceConnector.department_id.in_({c.department_id for c in cameras})
                )
            )
        ).scalars().all()
        connector_for = {c.department_id: c for c in connectors}

        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def run(camera: Camera) -> tuple[Camera, StreamEndpoint | None, StreamMetadata | None]:
            endpoint = by_camera.get(camera.id)
            if endpoint is None:
                return camera, None, None
            secret, cookie, header = await self._auth_for(
                endpoint, connector_for.get(camera.department_id)
            )
            async with semaphore:
                result = await self.enricher.enrich(
                    endpoint.url, secret=secret, cookie_name=cookie, header_name=header
                )
            return camera, endpoint, result

        # Fan out on the network, fan in on the database: AsyncSession is not
        # concurrency-safe, so nothing below this line runs inside a gather.
        probed = await asyncio.gather(*(run(c) for c in cameras))

        outcomes: list[EnrichmentOutcome] = []
        for camera, endpoint, result in probed:
            if endpoint is None:
                outcomes.append(
                    EnrichmentOutcome(
                        camera.id, camera.external_camera_id, False, {},
                        error="no enrichable stream endpoint",
                    )
                )
                continue

            payload = result.to_dict()
            before = {"codec": endpoint.codec, "resolution": endpoint.resolution}
            changed = False

            # Only overwrite with something actually measured. A failed probe must
            # not erase metadata a previous successful one established.
            if result.codec and endpoint.codec != result.codec:
                endpoint.codec = result.codec[:16]
                changed = True
            if result.resolution and endpoint.resolution != result.resolution:
                endpoint.resolution = result.resolution[:32]
                changed = True

            stream_meta = {k: v for k, v in payload.items() if k != "error"}
            if stream_meta:
                metadata = dict(camera.metadata_ or {})
                if metadata.get("stream") != stream_meta:
                    metadata["stream"] = stream_meta
                    camera.metadata_ = metadata
                    changed = True

            if changed:
                self.audit.record(
                    action="camera.enriched",
                    entity_type="camera",
                    entity_id=camera.id,
                    actor=actor,
                    before=before,
                    after={"codec": endpoint.codec, "resolution": endpoint.resolution},
                )

            outcomes.append(
                EnrichmentOutcome(
                    camera.id, camera.external_camera_id, changed, payload,
                    error=result.error,
                )
            )
        return skipped + outcomes


__all__ = ["EnrichmentOutcome", "MetadataService"]
