import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import DEFAULT_USER_AGENT

from app.core.enums import CameraStatus

# Any of these means the CDN bounced us somewhere else -- in practice the login page,
# because the session cookie expired. That is a fact about us, not about the camera.
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


@dataclass
class ProbeResult:
    status: CameraStatus
    latency_ms: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class HlsProbe:
    """Tier 2 of the probe ladder: fetch the HLS manifest and check it lists segments.

    Cheap (a few KB), works through the CDN on any network, and unlike a TCP connect it
    proves the gateway is actually producing media rather than merely accepting sockets.
    """

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.transport = transport
        self.timeout = timeout

    async def check(
        self,
        url: str,
        secret: str | None = None,
        cookie_name: str | None = None,
        header_name: str | None = None,
    ) -> ProbeResult:
        """Probe one endpoint.

        Auth is supplied per call rather than held on the prober: each stream
        endpoint carries its own credential_ref, and the scheme comes from the
        connector that owns it. A single shared cookie was a Sentinel-shaped
        assumption that does not survive a second department.
        """
        cookies = {cookie_name: secret} if (secret and cookie_name) else None
        # Without a browser-shaped agent this gateway answers 403, which would
        # be recorded as a fleet-wide outage rather than as our own request
        # being refused.
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        if secret and header_name:
            headers[header_name] = secret
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=self.timeout,
                cookies=cookies,
                headers=headers,
                # follow_redirects=False is load-bearing, not a default left alone: the
                # 3xx is the signal. Followed, the login page would come back 200 with
                # no #EXTINF in it and every camera behind an expired session would be
                # recorded OFFLINE -- a password change painting the whole fleet red.
                follow_redirects=False,
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            return ProbeResult(
                status=CameraStatus.OFFLINE,
                detail={"error": type(exc).__name__, "message": str(exc)},
            )

        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code in _REDIRECT_CODES:
            return ProbeResult(
                status=CameraStatus.UNKNOWN,
                latency_ms=latency_ms,
                detail={
                    "reason": "redirected, likely an expired session",
                    "location": response.headers.get("location", ""),
                },
            )

        if response.status_code != 200:
            return ProbeResult(
                status=CameraStatus.OFFLINE,
                latency_ms=latency_ms,
                detail={"http_status": response.status_code},
            )

        # A manifest with no #EXTINF is a live URL serving nothing: the gateway answers
        # but the encoder has stopped. That is genuinely a down camera.
        if "#EXTINF" not in response.text:
            return ProbeResult(
                status=CameraStatus.OFFLINE,
                latency_ms=latency_ms,
                detail={"reason": "manifest returned with no segments"},
            )

        return ProbeResult(status=CameraStatus.ONLINE, latency_ms=latency_ms)
