from typing import Any
from uuid import UUID

import httpx

from app.core.config import settings
from app.core.enums import Reachability, SourceType, StreamProtocol
from app.schemas.ingestion import RawCameraRecord

# The integrator's guide is explicit: "the catalogue is the contract, the URL pattern
# is not." We therefore read every endpoint from the catalogue and never template one.
#
# Reachability is not cosmetic: it is what Models 2-4 use to pick an endpoint they can
# actually open. HLS is served by a password-gated CDN and works on any network;
# RTSP and WHEP are served from a bare public IP on non-standard ports, so they only
# work where the gateway allows those ports out.
# Only used for the provenance label in source_ref. The dedupe key is resolved by
# the department's field_mappings, so a catalogue naming its id differently still
# onboards correctly -- this just keeps the trace label meaningful.
# For the source_ref provenance label. `name` is a last resort here: a human label
# still beats "sentinel:None" when tracing where a row came from.
_ID_KEYS = ("id", "camera_id", "cam_id", "camera_ref", "name")

# For building stream URLs. Deliberately excludes `name` -- a display name like
# "01 Chiman bhai Bridge" is not a path segment, and templating it would produce a
# URL that looks plausible and 404s.
_URL_ID_KEYS = ("id", "camera_id", "cam_id", "camera_ref")

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
        """Build this camera's stream endpoints.

        The integrator's guide says the catalogue carries all three URLs, and that
        "the catalogue is the contract, the URL pattern is not". The live catalogue
        does not: each entry holds only `id` and `name`. So a URL present in the entry
        still wins -- the guide's rule holds where it can -- and otherwise the URL is
        templated from the documented pattern, which is the only way to reach a camera
        at all. Templates come from settings because the host moves between the sandbox
        and the production round.
        """
        camera_id = next(
            (entry[key] for key in _URL_ID_KEYS if entry.get(key) not in (None, "")),
            None,
        )
        templates = {
            StreamProtocol.HLS.value: settings.sentinel_hls_template,
            StreamProtocol.RTSP.value: settings.sentinel_rtsp_template,
            StreamProtocol.WHEP.value: settings.sentinel_whep_template,
        }

        endpoints: list[dict[str, Any]] = []
        for key, (protocol, reachability, requires_auth) in _PROTOCOL_KEYS.items():
            url = entry.get(key)
            if not url:
                if camera_id is None:
                    continue
                url = templates[protocol.value].format(id=camera_id)
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
            camera_id = next(
                (
                    entry[key]
                    for key in _ID_KEYS
                    if entry.get(key) not in (None, "")
                ),
                None,
            )
            endpoints = self.endpoints_for(entry)
            # Drop the raw stream keys: they are represented authoritatively in
            # stream_endpoints now, and leaving them in the payload would file a second
            # copy into cameras.metadata via passthrough that goes stale on re-sync.
            payload = {k: v for k, v in entry.items() if k not in _PROTOCOL_KEYS}
            # Keys starting with "_" are skipped by FieldMappingResolver, so this rides
            # through the pipeline without polluting metadata. IngestionService._persist
            # reads it to write stream_endpoints rows.
            payload["_stream_endpoints"] = endpoints
            records.append(
                RawCameraRecord(
                    payload=payload,
                    department_id=department_id,
                    source_type=SourceType.ADAPTER,
                    source_ref=f"sentinel:{camera_id}",
                )
            )
        return records
