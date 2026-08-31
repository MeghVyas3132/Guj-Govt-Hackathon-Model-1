from typing import Any
from uuid import UUID

import httpx

from app.core.enums import Reachability, SourceType, StreamProtocol
from app.schemas.ingestion import RawCameraRecord

# The integrator's guide is explicit: "the catalogue is the contract, the URL pattern
# is not." We therefore read every endpoint from the catalogue and never template one.
#
# Reachability is not cosmetic: it is what Models 2-4 use to pick an endpoint they can
# actually open. HLS is served by a password-gated CDN and works on any network;
# RTSP and WHEP are served from a bare public IP on non-standard ports, so they only
# work where the gateway allows those ports out.
_PROTOCOL_KEYS: dict[str, tuple[StreamProtocol, Reachability, bool]] = {
    "hls": (StreamProtocol.HLS, Reachability.PUBLIC_CDN, True),
    "rtsp": (StreamProtocol.RTSP, Reachability.DIRECT_IP, False),
    "whep": (StreamProtocol.WHEP, Reachability.DIRECT_IP, False),
}


class SentinelAdapter:
    """Pulls the Sentinel sandbox catalogue and turns it into RawCameraRecords.

    Like every adapter this does no normalization: catalogue keys are handed to
    IngestionService untouched and translated by the department's field_mappings
    config, so a change in the catalogue's field names is a config edit, not a
    code change here.
    """

    code = "sentinel"

    def __init__(
        self,
        catalogue_url: str,
        session_cookie: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.catalogue_url = catalogue_url
        self.session_cookie = session_cookie
        self.transport = transport

    async def _get_catalogue(self) -> list[dict[str, Any]]:
        cookies = {"session": self.session_cookie} if self.session_cookie else None
        async with httpx.AsyncClient(
            transport=self.transport, timeout=30.0, cookies=cookies
        ) as client:
            response = await client.get(self.catalogue_url)
            response.raise_for_status()
            body = response.json()

        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            for key in ("cameras", "items", "data"):
                if isinstance(body.get(key), list):
                    return body[key]
            raise ValueError(f"Unrecognised catalogue shape: keys={list(body)}")
        raise ValueError(f"Unrecognised catalogue shape: {type(body).__name__}")

    def endpoints_for(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        endpoints: list[dict[str, Any]] = []
        for key, (protocol, reachability, requires_auth) in _PROTOCOL_KEYS.items():
            url = entry.get(key)
            if not url:
                continue
            endpoints.append(
                {
                    "protocol": protocol.value,
                    "url": url,
                    "codec": entry.get("codec"),
                    "resolution": entry.get("resolution"),
                    "reachability": reachability.value,
                    "requires_auth": requires_auth,
                    "credential_ref": "sentinel_cdn_password" if requires_auth else None,
                    "is_primary": protocol is StreamProtocol.HLS,
                }
            )
        return endpoints

    async def fetch(self, department_id: UUID) -> list[RawCameraRecord]:
        entries = await self._get_catalogue()
        records: list[RawCameraRecord] = []
        for entry in entries:
            camera_id = entry.get("id")
            payload = dict(entry)
            # Keys starting with "_" are skipped by FieldMappingResolver, so this rides
            # through the pipeline without polluting metadata. Plan 2 reads it in
            # IngestionService._persist to write stream_endpoints rows.
            payload["_stream_endpoints"] = self.endpoints_for(entry)
            records.append(
                RawCameraRecord(
                    payload=payload,
                    department_id=department_id,
                    source_type=SourceType.ADAPTER,
                    source_ref=f"sentinel:{camera_id}",
                )
            )
        return records
