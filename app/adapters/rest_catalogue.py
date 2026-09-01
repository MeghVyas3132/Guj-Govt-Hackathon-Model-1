"""A REST catalogue adapter driven entirely by configuration.

Nothing here knows what "sentinel" is, nor that hls/rtsp/whep exist. The
connector row supplies the URL, the auth scheme, where the camera list lives in
the response, which key is the id, and one rule per stream protocol. Onboarding
a new vendor is therefore a row, not a subclass.
"""

from typing import Any
from uuid import UUID

import httpx

from app.core.enums import SourceType
from app.schemas.connector import ConnectorConfig, EndpointRule
from app.schemas.ingestion import RawCameraRecord

# Fallbacks when root_path is unset and the payload is an object rather than an array.
_COMMON_ROOTS = ("cameras", "items", "data", "results")


class RestCatalogueAdapter:
    def __init__(
        self,
        config: ConnectorConfig,
        secret: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        code: str = "source",
    ) -> None:
        self.config = config
        self.secret = secret
        self.transport = transport
        self.code = code

    def _auth(self) -> tuple[dict[str, str], dict[str, str]]:
        """Returns (headers, cookies) for the configured scheme."""
        auth = self.config.auth
        if auth.type == "none" or not self.secret:
            return {}, {}
        try:
            # HTTP header and cookie values are latin-1. httpx raises deep in its
            # encoding layer otherwise, which surfaces to the operator as an
            # unrelated-looking UnicodeEncodeError with no mention of which
            # credential is at fault.
            self.secret.encode("latin-1")
        except UnicodeEncodeError:
            raise ValueError(
                f"credential {auth.credential_ref!r} contains characters that "
                f"cannot be sent in an HTTP header; it must be latin-1"
            ) from None
        if auth.type == "cookie":
            return {}, {auth.name: self.secret}
        if auth.type == "header":
            return {auth.name: self.secret}, {}
        if auth.type == "bearer":
            return {"Authorization": f"Bearer {self.secret}"}, {}
        if auth.type == "basic":
            import base64

            token = base64.b64encode(self.secret.encode()).decode()
            return {"Authorization": f"Basic {token}"}, {}
        return {}, {}

    def _extract(self, body: Any) -> list[dict[str, Any]]:
        if self.config.root_path:
            node = body
            for part in self.config.root_path.split("."):
                if not isinstance(node, dict):
                    raise ValueError(
                        f"root_path {self.config.root_path!r} does not resolve: "
                        f"{part!r} is not reachable"
                    )
                node = node.get(part)
            if not isinstance(node, list):
                raise ValueError(
                    f"root_path {self.config.root_path!r} did not yield a list"
                )
            return self._check_entries(node)

        if isinstance(body, list):
            return self._check_entries(body)
        if not isinstance(body, dict):
            raise ValueError(
                f"Unrecognised catalogue payload of type {type(body).__name__}"
            )
        for key in _COMMON_ROOTS:
            if isinstance(body.get(key), list):
                return self._check_entries(body[key])
        raise ValueError(f"Unrecognised catalogue shape: keys={sorted(body)}")

    @staticmethod
    def _check_entries(entries: list[Any]) -> list[dict[str, Any]]:
        """Reject a list of anything other than objects.

        Some catalogues return bare ids (`["cam01", "cam02"]`). That cannot be
        onboarded -- there is nowhere to read a name or a location from -- but it
        has to say so, because without this the first non-dict crashes the whole
        department's sync with an AttributeError raised from a comprehension.
        """
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"catalogue entry {index} is {type(entry).__name__}, expected an "
                    f"object; a list of bare ids cannot be onboarded"
                )
        return entries

    async def _get_catalogue(self) -> list[dict[str, Any]]:
        headers, cookies = self._auth()
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=self.config.request_timeout_s,
            headers=headers,
            cookies=cookies,
            follow_redirects=False,
        ) as client:
            response = await client.get(self.config.catalogue_url)
            response.raise_for_status()
            return self._extract(response.json())

    def _camera_id(self, entry: dict[str, Any]) -> str | None:
        for key in self.config.id_keys:
            value = entry.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    def endpoints_for(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        camera_id = self._camera_id(entry)
        endpoints: list[dict[str, Any]] = []
        for rule in self.config.endpoint_rules:
            url = entry.get(rule.url_key) if rule.url_key else None
            if not url:
                if not rule.url_template or camera_id is None:
                    continue
                try:
                    url = rule.url_template.format(id=camera_id)
                except (KeyError, IndexError):
                    # A placeholder we cannot fill. The config validator rejects
                    # these on write, so this only catches rows written before it
                    # existed -- skip the one endpoint rather than losing the
                    # camera and every other endpoint it has.
                    continue
            endpoints.append(
                {
                    "protocol": rule.protocol.value,
                    "url": url,
                    "codec": entry.get("codec"),
                    "resolution": entry.get("resolution"),
                    "reachability": rule.reachability.value,
                    "requires_auth": rule.requires_auth,
                    "credential_ref": rule.credential_ref,
                    "is_primary": rule.is_primary,
                }
            )
        return endpoints

    def _consumed_keys(self) -> set[str]:
        """Keys the endpoint rules read, so they are not also filed into metadata
        as a second copy that goes stale on the next sync."""
        return {r.url_key for r in self.config.endpoint_rules if r.url_key}

    async def fetch(self, department_id: UUID) -> list[RawCameraRecord]:
        entries = await self._get_catalogue()
        consumed = self._consumed_keys()
        records: list[RawCameraRecord] = []
        for entry in entries:
            payload = {k: v for k, v in entry.items() if k not in consumed}
            payload["_stream_endpoints"] = self.endpoints_for(entry)
            records.append(
                RawCameraRecord(
                    payload=payload,
                    department_id=department_id,
                    source_type=SourceType.ADAPTER,
                    source_ref=f"{self.code}:{self._camera_id(entry)}",
                )
            )
        return records


__all__ = ["EndpointRule", "RestCatalogueAdapter"]
