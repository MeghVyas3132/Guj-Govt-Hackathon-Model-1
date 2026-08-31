import time
from dataclasses import dataclass, field
from typing import Any

import httpx

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
        session_cookie: str | None = None,
    ) -> None:
        self.transport = transport
        self.timeout = timeout
        self.session_cookie = session_cookie

    async def check(self, url: str) -> ProbeResult:
        cookies = {"session": self.session_cookie} if self.session_cookie else None
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=self.timeout,
                cookies=cookies,
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
