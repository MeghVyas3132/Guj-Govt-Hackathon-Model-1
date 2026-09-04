"""
ml.config
=========
Centralised settings for the Setu ML worker.

Reads values from the environment (or a .env file placed next to the process
working directory).  Every field maps 1-to-1 with the variables documented in
.env.example.

Usage::

    from ml.config import settings

    print(settings.sentinel_base_url)
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime-configurable parameters for the Setu ML worker.

    Pydantic-settings automatically reads values from environment variables
    *and* from a ``.env`` file (case-insensitive, ``extra="ignore"`` so
    unrelated env vars don't cause errors).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Sentinel camera sandbox ──────────────────────────────────────────────
    sentinel_email: str
    sentinel_password: str
    """Password for POST /auth/login on the Sentinel sandbox.

    Deliberately has no default: a shared credential that ships in the source is
    a credential that leaks. Set SENTINEL_PASSWORD in .env, which is gitignored.
    """

    sentinel_base_url: str = "https://cctv.corp8.cloud"
    """Base URL for the Sentinel HLS sandbox (no trailing slash)."""

    sentinel_rtsp_base: str = "rtsp://103.250.160.189:8554/stream"
    """Base URL for RTSP streams (no auth required)."""

    # ── Camera selection ─────────────────────────────────────────────────────
    cameras: str = "cam04,cam05,cam06,cam08,cam12,cam18"
    """Comma-separated camera IDs to process, or ``"all"`` for every camera."""

    frame_skip: int = 5
    """Process every Nth frame (5 ≈ 6 FPS at 30 FPS source)."""

    # ── AI models ────────────────────────────────────────────────────────────
    yolo_model: str = "yolov8s.pt"
    clip_model: str = "ViT-B-32"
    clip_pretrained: str = "openai"
    plate_model: str = "FarAwayFer/yolov8n_license_plate_recognition"
    plate_ocr_model: str = "global-plates-mobile-vit-v2-model"
    plate_conf_threshold: float = 0.5
    detection_conf_threshold: float = 0.35

    yolo_imgsz: int = 1280
    """Inference resolution for the vehicle detector.

    Ultralytics defaults to 640, which letterboxes a 1920x1080 frame down by 3x
    before the model sees it — a vehicle 30 px wide at the far end of a junction
    becomes 10 px and drops below the detector's smallest head. Since these feeds
    are 1080p, 640 throws away the resolution we have. 1280 costs about 4x the
    FLOPs and finds the distant vehicles that make up most of the frame.
    """

    min_plate_width_px: int = 60
    """Reject plate crops narrower than this before OCR.

    Industry guidance for ANPR is 100-150 px of plate width. Below roughly 60
    there is nothing left to read, and the OCR returns confident nonsense that
    would outvote the good reads during per-track voting.
    """

    # ── Per-track aggregation ────────────────────────────────────────────────
    track_keep_best: int = 3
    """How many of a track's sharpest crops to keep for embedding and ANPR."""

    max_plate_reads_per_track: int = 25
    """Cap on OCR attempts per vehicle.

    Voting needs many reads to beat the compression, but a vehicle queuing at a
    signal can stay in frame for hundreds of frames, and reads past the first
    couple of dozen add cost without changing the outcome.
    """

    track_idle_frames: int = 45
    """Processed frames without a sighting before a track is considered finished.

    Must exceed the tracker's own track_buffer (30), or a vehicle briefly hidden
    behind a bus is closed and reopened as two separate vehicles.
    """

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = "postgresql://setu:setu_dev_only@localhost:5432/setu"

    # ── MinIO / S3 ───────────────────────────────────────────────────────────
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "setu"
    minio_secret_key: str = "setu_dev_only"
    minio_bucket: str = "setu-clips"
    minio_secure: bool = False

    # ── Matching thresholds ──────────────────────────────────────────────────
    watchlist_embed_threshold: float = 0.75

    # ── Stream / reconnect tuning ────────────────────────────────────────────
    stream_reconnect_base_s: float = 2.0
    """Initial back-off delay (seconds) before the first reconnect attempt."""

    stream_reconnect_max_s: float = 30.0
    """Maximum back-off delay (seconds) for reconnect attempts."""

    rtsp_port: int = 8554
    """Port used by the RTSP server (for reachability probes)."""

    force_rtsp: bool = False
    """Set to true to prefer RTSP over HLS (faster but susceptible to grey smear on VOD streams)."""

    rtsp_open_timeout_ms: int = 5_000
    """cv2.CAP_PROP_OPEN_TIMEOUT_MSEC for RTSP captures."""

    rtsp_read_timeout_ms: int = 5_000
    """cv2.CAP_PROP_READ_TIMEOUT_MSEC for RTSP captures."""

    # ── Derived helpers ──────────────────────────────────────────────────────

    @field_validator("cameras", mode="before")
    @classmethod
    def _strip_cameras(cls, v: str) -> str:
        """Strip surrounding whitespace from the cameras string."""
        return v.strip()

    def camera_ids(self) -> list[str]:
        """Return the list of camera IDs parsed from :attr:`cameras`.

        Supports the special value ``"all"`` which expands to cam01 … cam30
        (the full Sentinel sandbox set).
        """
        if self.cameras.lower() == "all":
            return [f"cam{i:02d}" for i in range(1, 31)]
        return [c.strip() for c in self.cameras.split(",") if c.strip()]

    def rtsp_url(self, cam_id: str) -> str:
        """Build an RTSP stream URL for *cam_id*."""
        import urllib.parse
        if self.sentinel_rtsp_base.startswith("rtsp://") and self.sentinel_email:
            encoded_email = urllib.parse.quote(self.sentinel_email, safe='')
            base_no_scheme = self.sentinel_rtsp_base[7:]
            return f"rtsp://{encoded_email}:{self.sentinel_password}@{base_no_scheme}/{cam_id}"
        return f"{self.sentinel_rtsp_base}/{cam_id}"

    def hls_url(self, cam_id: str) -> str:
        """Build an HLS playlist URL for *cam_id*."""
        return f"{self.sentinel_base_url}/{cam_id}/index.m3u8"


# Module-level singleton — import and use directly:
#   from ml.config import settings
settings = Settings()
