"""A narrow relay so an operator can confirm a camera is actually working.

Why this has to exist at all: the gateway sends no `Access-Control-Allow-Origin`
header, and its session cookie is `HttpOnly; Secure; SameSite=Lax`. A browser on
the registry's origin therefore cannot fetch the manifest, and could not attach
the credential if it could. Playing a feed in the portal is impossible without
something server-side holding the credential and re-serving same-origin.

What this deliberately is not: a VMS. Model 1's job is metadata and asset
visibility, and video does not otherwise pass through it. This relays on demand,
only for a camera already in the registry, only to a caller holding
`cameras:read`, and it is bounded by `MAX_BODY` so a proxied segment cannot be
used to pull an arbitrary payload through the service.
"""

from urllib.parse import urljoin, urlparse

import httpx

# Big enough for an HLS segment at broadcast bitrates, small enough that this
# cannot be turned into a general-purpose file relay.
MAX_BODY = 24 * 1024 * 1024

# The manifest is text and gets rewritten; everything else is streamed through.
MANIFEST_TYPES = ("application/vnd.apple.mpegurl", "application/x-mpegurl")

TIMEOUT = 20.0

# The gateway serves .ts segments as "text/vnd.trolltech.linguist" -- it has
# guessed from the extension that they are Qt Linguist files. A player that
# trusts Content-Type refuses those, so the type is decided from the extension
# here rather than taken on faith.
_BY_EXTENSION = {
    ".ts": "video/mp2t",
    ".m4s": "video/iso.segment",
    ".mp4": "video/mp4",
    ".aac": "audio/aac",
    ".vtt": "text/vtt",
    ".key": "application/octet-stream",
}


def content_type_for(url: str, upstream: str) -> str:
    """Trust the extension over the gateway, which is frequently wrong."""
    path = urlparse(url).path.lower()
    for suffix, media_type in _BY_EXTENSION.items():
        if path.endswith(suffix):
            return media_type
    if any(t in upstream for t in MANIFEST_TYPES):
        return upstream
    return upstream or "application/octet-stream"


class UpstreamError(Exception):
    """The gateway refused or could not be reached."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _same_origin(base: str, candidate: str) -> bool:
    """Is `candidate` on the same scheme+host+port as `base`?

    This is the SSRF guard. Sub-path requests arrive from the browser, so
    without it a caller could hand us `http://169.254.169.254/...` and use the
    registry's own network position to read something it should not.
    """
    a, b = urlparse(base), urlparse(candidate)
    return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)


def rewrite_manifest(body: str, manifest_url: str, proxy_prefix: str) -> str:
    """Point every URL in a manifest back at us.

    Three kinds of reference need rewriting, and missing any one of them breaks
    playback in a way that looks like a codec problem:

      - segment lines (`seg00000.ts`), relative to the manifest
      - `#EXT-X-KEY:URI="/enc.key"`, which is absolute on this gateway and would
        otherwise be fetched cross-origin without the credential
      - `#EXT-X-STREAM-INF` variant playlists, which are themselves manifests

    `proxy_prefix` ends without a slash; the caller appends the encoded target.
    """
    out: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()

        if not line:
            out.append(raw)
            continue

        if line.startswith("#EXT-X-KEY") or line.startswith("#EXT-X-SESSION-KEY"):
            # Rewrite only the URI attribute, leaving METHOD and IV untouched.
            start = line.find('URI="')
            if start != -1:
                end = line.find('"', start + 5)
                if end != -1:
                    target = urljoin(manifest_url, line[start + 5 : end])
                    line = (
                        line[: start + 5]
                        + f"{proxy_prefix}?target={_quote(target)}"
                        + line[end:]
                    )
            out.append(line)
            continue

        if line.startswith("#"):
            out.append(raw)
            continue

        # A bare line is a URI: a segment, or a variant playlist.
        target = urljoin(manifest_url, line)
        out.append(f"{proxy_prefix}?target={_quote(target)}")

    return "\n".join(out) + "\n"


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


class StreamProxy:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def fetch(
        self,
        url: str,
        *,
        allowed_origin: str,
        secret: str | None = None,
        cookie_name: str | None = None,
        header_name: str | None = None,
    ) -> tuple[bytes, str]:
        """Fetch one upstream resource. Returns (body, content_type)."""
        if not _same_origin(allowed_origin, url):
            raise UpstreamError(400, "Target is not on the camera's own stream host")

        cookies = {cookie_name: secret} if (secret and cookie_name) else None
        headers = {header_name: secret} if (secret and header_name) else None

        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=TIMEOUT,
                cookies=cookies,
                headers=headers,
                # A redirect here is the login page, exactly as for the health
                # probe. Following it would hand the player an HTML body and
                # present as a corrupt stream.
                follow_redirects=False,
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise UpstreamError(502, f"{type(exc).__name__}: {exc}") from exc

        if response.status_code in (301, 302, 303, 307, 308):
            raise UpstreamError(
                502, "Gateway redirected, which usually means the session expired"
            )
        if response.status_code >= 400:
            raise UpstreamError(502, f"Gateway returned HTTP {response.status_code}")

        body = response.content
        if len(body) > MAX_BODY:
            raise UpstreamError(502, "Upstream response exceeds the proxy size limit")

        upstream_type = response.headers.get("content-type", "")
        return body, content_type_for(url, upstream_type)


__all__ = [
    "MANIFEST_TYPES",
    "StreamProxy",
    "UpstreamError",
    "content_type_for",
    "rewrite_manifest",
]
