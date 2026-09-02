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
from urllib.parse import urljoin

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.core.config import DEFAULT_USER_AGENT

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
    key_iv: str | None = None
    first_segment: str | None = None
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
                if meta.first_segment is None:
                    meta.first_segment = line
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
            meta.key_iv = attrs.get("IV")
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


def _describe(exc: Exception, stage: str) -> str:
    """A transport error that says which step failed.

    httpx timeout exceptions carry an empty message, so the bare repr reads as
    "ReadTimeout: " -- which tells an operator nothing about whether the
    manifest, the key or the segment was the problem.
    """
    detail = str(exc).strip()
    return f"{type(exc).__name__} fetching {stage}" + (f": {detail}" if detail else "")


def _decrypt_aes128(payload: bytes, key: bytes, iv_hex: str | None) -> bytes:
    """Undo HLS AES-128-CBC on one segment.

    When the manifest states no IV, HLS defines it as the segment's media
    sequence number. We always take the first segment, so that is zero.

    PKCS#7 padding is stripped only when it is actually valid: a segment cut
    short mid-transfer would otherwise have real bytes trimmed off its end, and
    the decoder would report a corrupt stream rather than a truncated download.
    """
    iv = bytes(16)
    if iv_hex:
        cleaned = iv_hex.lower().removeprefix("0x")
        try:
            candidate = bytes.fromhex(cleaned)
        except ValueError:
            candidate = b""
        if len(candidate) == 16:
            iv = candidate

    if len(payload) % 16:
        raise ValueError("encrypted segment is not a whole number of AES blocks")

    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = decryptor.update(payload) + decryptor.finalize()

    if plain:
        pad = plain[-1]
        if 1 <= pad <= 16 and plain[-pad:] == bytes([pad]) * pad:
            plain = plain[:-pad]
    return plain


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


# The decryption key is very often one file shared by every camera on a gateway
# -- the Sentinel sandbox serves a single /enc.key for all thirty. Fetching it
# once instead of per camera removes a full round-trip from all but the first,
# which on an 11-second-latency gateway is the difference between a fleet run
# finishing and timing out. Bounded so it cannot grow without limit.
_KEY_CACHE: dict[str, bytes] = {}
_KEY_CACHE_MAX = 256

# How much of a segment to fetch. A decoder needs only the beginning of an
# MPEG-TS to report codec, resolution and frame rate, and segment sizes vary
# wildly on one gateway -- 268KB on one sandbox camera, 2.7MB on another.
# Pulling whole segments made the largest cameras the ones that always timed
# out, which is exactly backwards: they are no harder to describe, only slower
# to download. 384KB covers the PAT/PMT and several frames.
SEGMENT_PREFIX_BYTES = 384 * 1024


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
        media_timeout: float = 45.0,
        decode_timeout: float = 15.0,
        max_retries: int = 2,
        retry_backoff_s: float = 2.0,
    ) -> None:
        self.transport = transport
        self.timeout = timeout
        # Two different budgets, because they are two different operations.
        #
        # `media_timeout` bounds one *network* fetch of a segment or a key. The
        # gateway answers in ~11s cold and worse under load, so this has to be
        # generous -- but not so generous that a stuck request eats the retry
        # budget. Three attempts at 45s is a bounded 141s worst case; at 90s it
        # was 276s, and a fleet pass could not finish.
        #
        # `decode_timeout` bounds ffprobe, which now reads bytes already in
        # memory off a pipe. That takes milliseconds. Waiting 90s for it is pure
        # waste, and if it ever does hang, something is wrong that more waiting
        # will not fix.
        self.media_timeout = media_timeout
        self.decode_timeout = decode_timeout
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s
        self.probe_media = probe_media
        self.ffprobe_path = ffprobe_path or shutil.which("ffprobe")

    async def enrich(
        self,
        url: str,
        secret: str | None = None,
        cookie_name: str | None = None,
        header_name: str | None = None,
    ) -> StreamMetadata:
        # The manifest goes through the same retrying fetch as the segment. It
        # used to have its own inline request with a shorter timeout and no
        # retry at all, which made it the failure point on an erratic gateway --
        # the segment path was hardened and the step before it was not.
        try:
            raw = await self._fetch(
                url, secret, cookie_name, header_name, stage="manifest"
            )
        except httpx.HTTPError as exc:
            return StreamMetadata(error=_describe(exc, "manifest"))
        except ValueError as exc:
            return StreamMetadata(error=str(exc))

        manifest = parse_manifest(raw.decode("utf-8", errors="replace"))
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

    async def _fetch(
        self,
        url: str,
        secret: str | None,
        cookie_name: str | None,
        header_name: str | None,
        prefix_bytes: int | None = None,
        stage: str = "segment",
    ) -> bytes:
        """Fetch one resource, retrying a timeout with backoff.

        Retries only timeouts and connection errors, never an HTTP status: a 404
        is a settled fact and re-asking wastes a slot on a gateway that is
        already the bottleneck. Measured against the sandbox, latency climbs
        under sustained load -- roughly 11s cold, and worse the longer a run
        goes on -- so a single slow response is the normal case rather than a
        failure, and giving up on the first one loses cameras that would have
        succeeded a moment later.
        """
        cookies = {cookie_name: secret} if (secret and cookie_name) else None
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        if secret and header_name:
            headers[header_name] = secret
        if prefix_bytes:
            # A gateway free to ignore this returns 200 and the whole body, which
            # still works -- just slower. Nothing depends on the range being
            # honoured.
            headers["Range"] = f"bytes=0-{prefix_bytes - 1}"

        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                await asyncio.sleep(self.retry_backoff_s * (2 ** (attempt - 1)))
            try:
                async with httpx.AsyncClient(
                    transport=self.transport,
                    timeout=self.media_timeout,
                    cookies=cookies,
                    headers=headers,
                    follow_redirects=False,
                ) as client:
                    response = await client.get(url)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
                last = exc
                continue
            if response.status_code >= 300:
                raise ValueError(f"HTTP {response.status_code} fetching {url}")
            body = response.content
            # Trim a gateway that ignored the Range header, so the cost of a
            # 2.7MB segment is paid once rather than on every retry.
            return body[:prefix_bytes] if prefix_bytes else body

        raise last if last else ValueError(f"could not fetch {stage}")

    async def _segment_bytes(
        self,
        manifest_url: str,
        manifest: ManifestMetadata,
        secret: str | None,
        cookie_name: str | None,
        header_name: str | None,
    ) -> bytes:
        """One decrypted media segment, ready for a decoder."""
        segment_url = urljoin(manifest_url, manifest.first_segment or "")
        payload = await self._fetch(
            segment_url, secret, cookie_name, header_name,
            prefix_bytes=SEGMENT_PREFIX_BYTES,
        )

        if not manifest.encryption:
            return payload
        if manifest.encryption != "AES-128":
            # SAMPLE-AES encrypts inside the container and cannot be undone with
            # a whole-buffer decrypt. Say so rather than returning noise that
            # ffprobe reports as a corrupt stream.
            raise ValueError(f"cannot decrypt {manifest.encryption} segments")
        if not manifest.key_uri:
            raise ValueError("segment is encrypted but the manifest names no key")

        key_url = urljoin(manifest_url, manifest.key_uri)
        key = _KEY_CACHE.get(key_url)
        if key is None:
            key = await self._fetch(key_url, secret, cookie_name, header_name)
            if len(key) != 16:
                raise ValueError(f"AES-128 key is {len(key)} bytes, expected 16")
            if len(_KEY_CACHE) < _KEY_CACHE_MAX:
                _KEY_CACHE[key_url] = key

        # A ranged fetch lands mid-block. CBC decrypts from the start regardless,
        # so the trailing partial block is simply dropped -- and _decrypt_aes128
        # strips padding only when it is actually valid, so the truncated tail is
        # left alone rather than mistaken for padding.
        whole = payload[: len(payload) - (len(payload) % 16)]
        if not whole:
            raise ValueError("segment is shorter than one AES block")

        return _decrypt_aes128(whole, key, manifest.key_iv)

    async def _probe_media(
        self,
        url: str,
        result: StreamMetadata,
        secret: str | None,
        cookie_name: str | None,
        header_name: str | None,
    ) -> None:
        """Decode one segment to read the stream's real parameters.

        The segment is fetched here rather than by ffmpeg. Handing ffprobe the
        playlist URL makes it do its own networking, and on a slow gateway that
        is three serial round-trips it controls and we cannot bound: the whole
        playlist (216KB and 7,200 entries for a 12-hour archive), then the key,
        then a segment. Measured against the sandbox that is ~29 seconds for one
        camera with no other load, which is why a fleet run timed out.

        Fetching the parts ourselves costs two requests instead of three, lets
        the key be cached across cameras that share one, and leaves ffprobe
        reading local bytes off a pipe -- where it takes milliseconds and cannot
        time out on the network at all.
        """
        assert self.ffprobe_path
        manifest = result.manifest
        if manifest is None or not manifest.first_segment:
            result.error = "manifest lists no segment to decode"
            return

        try:
            payload = await self._segment_bytes(url, manifest, secret, cookie_name, header_name)
        except httpx.HTTPError as exc:
            result.error = _describe(exc, "segment")
            return
        except ValueError as exc:
            result.error = str(exc)[:300]
            return

        args = [
            self.ffprobe_path, "-v", "error",
            "-probesize", "2000000",
            "-analyzeduration", "2000000",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,avg_frame_rate",
            "-of", "json",
            "-i", "pipe:0",
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload), timeout=self.decode_timeout
            )
        except TimeoutError:
            # communicate() leaves the child running when it times out.
            process.kill()
            await process.wait()
            result.error = f"ffprobe timed out after {self.decode_timeout:.0f}s"
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
