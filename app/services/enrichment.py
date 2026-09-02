"""Derive camera metadata from the stream itself.

The problem this exists to solve: a source catalogue often carries an id and a
name and nothing else. The Sentinel sandbox is the worked example -- 30 cameras,
two fields each -- but a registry needs codec, resolution and framerate to be
useful for planning, and no operator is going to type those in 80,000 times.

The stream knows. An HLS manifest states its segmenting, its encryption and
whether it is live or recorded, in plain text and for the cost of a few KB.
Decoding one segment yields the true codec and resolution. So we ask the stream
rather than the catalogue, and both tiers are optional: a camera that cannot be
probed keeps whatever the catalogue gave it.
"""

import asyncio
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

# "#EXT-X-KEY:METHOD=AES-128,URI="/enc.key",IV=0x00"
_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def _attrs(line: str) -> dict[str, str]:
    _, _, rest = line.partition(":")
    return {k: v.strip('"') for k, v in _ATTR.findall(rest)}


@dataclass
class ManifestMetadata:
    """What an HLS manifest states about itself, without decoding any media."""

    version: int | None = None
    target_duration: float | None = None
    playlist_type: str | None = None
    segment_count: int = 0
    total_duration_s: float | None = None
    encryption: str | None = None
    key_uri: str | None = None
    is_live: bool | None = None
    variants: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_master(self) -> bool:
        return bool(self.variants)


def parse_manifest(text: str) -> ManifestMetadata:
    """Read an HLS manifest. Pure, so it is testable without a network.

    Handles both playlist kinds: a master playlist advertises variants with
    resolution and codecs inline, which is the cheap path to that metadata; a
    media playlist lists segments, from which archive depth is derivable.
    """
    meta = ManifestMetadata()
    durations: list[float] = []
    pending_variant: dict[str, Any] | None = None
    has_endlist = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if not line.startswith("#"):
            # A URI line closes whatever tag preceded it.
            if pending_variant is not None:
                pending_variant["uri"] = line
                meta.variants.append(pending_variant)
                pending_variant = None
            else:
                meta.segment_count += 1
            continue

        if line.startswith("#EXT-X-VERSION:"):
            try:
                meta.version = int(line.split(":", 1)[1])
            except ValueError:
                pass
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            try:
                meta.target_duration = float(line.split(":", 1)[1])
            except ValueError:
                pass
        elif line.startswith("#EXT-X-PLAYLIST-TYPE:"):
            meta.playlist_type = line.split(":", 1)[1].strip()
        elif line.startswith("#EXT-X-KEY:"):
            attrs = _attrs(line)
            method = attrs.get("METHOD")
            # METHOD=NONE is the tag explicitly declaring no encryption.
            meta.encryption = None if method == "NONE" else method
            meta.key_uri = attrs.get("URI")
        elif line.startswith("#EXT-X-ENDLIST"):
            has_endlist = True
        elif line.startswith("#EXTINF:"):
            value = line.split(":", 1)[1].split(",")[0]
            try:
                durations.append(float(value))
            except ValueError:
                pass
        elif line.startswith("#EXT-X-STREAM-INF:"):
            attrs = _attrs(line)
            variant: dict[str, Any] = {}
            if "RESOLUTION" in attrs:
                variant["resolution"] = attrs["RESOLUTION"]
            if "CODECS" in attrs:
                variant["codecs"] = attrs["CODECS"]
            if "BANDWIDTH" in attrs:
                try:
                    variant["bandwidth"] = int(attrs["BANDWIDTH"])
                except ValueError:
                    pass
            if "FRAME-RATE" in attrs:
                try:
                    variant["frame_rate"] = float(attrs["FRAME-RATE"])
                except ValueError:
                    pass
            pending_variant = variant

    if durations:
        meta.total_duration_s = round(sum(durations), 3)
    # A VOD playlist is recorded by definition; otherwise ENDLIST is what marks a
    # stream as finished. Absent both, it is still being appended to: live.
    if meta.playlist_type == "VOD":
        meta.is_live = False
    elif meta.segment_count or durations:
        meta.is_live = not has_endlist
    return meta


@dataclass
class StreamMetadata:
    """Everything derived for one endpoint. Every field is optional: whatever the
    stream did not tell us stays None rather than becoming a fabricated default."""

    codec: str | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None
    manifest: ManifestMetadata | None = None
    error: str | None = None

    @property
    def resolution(self) -> str | None:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            k: v
            for k, v in {
                "codec": self.codec,
                "resolution": self.resolution,
                "frame_rate": self.frame_rate,
                "error": self.error,
            }.items()
            if v is not None
        }
        if self.manifest is not None:
            # `v not in (None, [], 0)` would drop is_live=False, because False == 0
            # in Python -- silently discarding the single most useful fact about a
            # recorded loop. Only absence is filtered here, never a measured value.
            out["manifest"] = {
                k: v
                for k, v in asdict(self.manifest).items()
                if v is not None and v != []
            }
        return out


def _fps(value: str | None) -> float | None:
    """ffprobe reports frame rate as the rational "30/1" -- and as "0/0" for a
    stream where it could not determine one."""
    if not value or "/" not in value:
        return None
    num, _, den = value.partition("/")
    try:
        numerator, denominator = float(num), float(den)
    except ValueError:
        return None
    return round(numerator / denominator, 3) if denominator else None


class StreamEnricher:
    """Two-tier enrichment: manifest first, media decode second.

    `ffprobe_path` is resolved rather than assumed. It is genuinely optional --
    a deployment without ffmpeg installed still gets the manifest tier, which is
    the one that needs no extra dependency.
    """

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
        ffprobe_path: str | None = None,
        probe_media: bool = True,
        media_timeout: float = 90.0,
    ) -> None:
        self.transport = transport
        self.timeout = timeout
        # Decoding media is not the same operation as fetching a manifest and
        # cannot share its budget. ffprobe has to parse the playlist -- 7,200
        # entries for a 12-hour archive -- then fetch the decryption key and one
        # encrypted segment before it can report a single frame. Under
        # concurrency that comfortably exceeds any sane HTTP timeout, and the
        # symptom is every camera reporting "ffprobe timed out".
        self.media_timeout = media_timeout
        self.probe_media = probe_media
        self.ffprobe_path = ffprobe_path or shutil.which("ffprobe")

    async def enrich(
        self,
        url: str,
        secret: str | None = None,
        cookie_name: str | None = None,
        header_name: str | None = None,
    ) -> StreamMetadata:
        cookies = {cookie_name: secret} if (secret and cookie_name) else None
        headers = {header_name: secret} if (secret and header_name) else None

        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=self.timeout,
                cookies=cookies,
                headers=headers,
                follow_redirects=False,
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            return StreamMetadata(error=f"{type(exc).__name__}: {exc}")

        if response.status_code >= 300:
            return StreamMetadata(error=f"HTTP {response.status_code}")

        manifest = parse_manifest(response.text)
        result = StreamMetadata(manifest=manifest)

        # A master playlist states resolution and codecs itself. Free, and exact.
        if manifest.variants:
            best = max(manifest.variants, key=lambda v: v.get("bandwidth", 0))
            if "resolution" in best and "x" in best["resolution"]:
                w, _, h = best["resolution"].partition("x")
                if w.isdigit() and h.isdigit():
                    result.width, result.height = int(w), int(h)
            if "codecs" in best:
                result.codec = best["codecs"].split(",")[0].split(".")[0] or None
            result.frame_rate = best.get("frame_rate")

        if result.codec is None and self.probe_media and self.ffprobe_path:
            await self._probe_media(url, result, secret, cookie_name, header_name)
        return result

    async def _probe_media(
        self,
        url: str,
        result: StreamMetadata,
        secret: str | None,
        cookie_name: str | None,
        header_name: str | None,
    ) -> None:
        """Decode enough of the stream to read its real parameters.

        This is the only way to get codec and resolution off a media playlist,
        which is what the Sentinel gateway serves. It costs one segment.
        """
        assert self.ffprobe_path
        args = [self.ffprobe_path, "-v", "error"]
        if secret and cookie_name:
            # ffprobe wants raw HTTP headers, CRLF-terminated.
            args += ["-headers", f"Cookie: {cookie_name}={secret}\r\n"]
        elif secret and header_name:
            args += ["-headers", f"{header_name}: {secret}\r\n"]
        args += [
            # Bounded so ffprobe stops as soon as it can describe the stream
            # rather than buffering for its default 5 seconds of media.
            "-probesize", "2000000",
            "-analyzeduration", "2000000",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,avg_frame_rate",
            "-of", "json",
            "-read_intervals", "%+#1",
            url,
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.media_timeout
            )
        except TimeoutError:
            # communicate() leaves the child running when it times out.
            process.kill()
            await process.wait()
            result.error = f"ffprobe timed out after {self.media_timeout:.0f}s"
            return
        except OSError as exc:
            result.error = f"ffprobe unavailable: {exc}"
            return

        if process.returncode != 0:
            result.error = (stderr.decode(errors="replace").strip() or "ffprobe failed")[:300]
            return

        try:
            streams = json.loads(stdout or b"{}").get("streams") or []
        except json.JSONDecodeError:
            result.error = "ffprobe returned unparseable output"
            return
        if not streams:
            result.error = "no video stream found"
            return

        stream = streams[0]
        result.codec = stream.get("codec_name") or None
        result.width = stream.get("width") or None
        result.height = stream.get("height") or None
        result.frame_rate = _fps(stream.get("avg_frame_rate"))


__all__ = ["ManifestMetadata", "StreamEnricher", "StreamMetadata", "parse_manifest"]
