"""
scripts/batch_index.py -- Setu offline batch indexer
=====================================================
Processes all 30 hackathon camera feeds sequentially and populates the
detections table with CLIP embeddings, colour buckets, and pts_ms for
video-seek.

Run this overnight AFTER resetting the schema (scripts/reset.py) and seeding
(scripts/seed.py). Do NOT run the live worker at the same time.

Usage:
    python -m scripts.batch_index
    python -m scripts.batch_index --cameras cam01,cam04,cam08
    python -m scripts.batch_index --start-time 06:00 --end-time 22:00
    python -m scripts.batch_index --resume          # skip cameras with detections already

Frame-rate strategy:
    --frame-skip 150  (default) approx 1 frame per 5 seconds at 30 FPS source.
    This reduces 30 cameras x 12 hours x 30 FPS (~19.4M frames) to ~260K
    sampled frames. With YOLO gating CLIP, CLIP only runs when a vehicle is
    detected -- effectively much less on quiet/night cameras.

Estimated runtime (CPU-only, laptop):
    ~90-150 seconds per camera depending on traffic density.
    30 cameras: 45-75 minutes total.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from ml.aggregate import TrackAggregator
from ml.auth import get_cookie
from ml.config import settings
from ml.db import commit, get_camera_id, get_connection, write_detection
from ml.models import warm_all_models
from ml.pipeline import DetectionPipeline
from ml.stream import CameraStream
from ml.watchlist import WatchlistChecker
from ml.worker import VEHICLE_CLASS_NAMES, finalise_track

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# frame skip for batch mode -- 1 frame per ~5 s at 30 FPS
BATCH_FRAME_SKIP = 150


def _get_detection_count(conn, camera_uuid: str) -> int:
    """Return the number of detections already stored for this camera."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM detections WHERE camera_id = %s", (camera_uuid,))
        row = cur.fetchone()
        return int(row["n"]) if row else 0


def index_camera(
    cam_external_id: str,
    cookie: str,
    start_time: str | None,
    end_time: str | None,
    resume: bool,
) -> dict:
    """
    Index a single camera. Returns a summary dict with keys:
        camera_id, frames_sampled, detections_written, duration_s, skipped
    """
    prefix = f"[{cam_external_id}]"
    log.info("%s -- Starting", prefix)

    conn = get_connection()
    pipeline = DetectionPipeline()
    watchlist = WatchlistChecker(conn)
    watchlist.refresh()

    camera_uuid = get_camera_id(conn, cam_external_id)
    if camera_uuid is None:
        log.error("%s Not in DB -- run scripts/seed.py first. Skipping.", prefix)
        conn.close()
        return {"camera_id": cam_external_id, "skipped": True, "reason": "not_in_db"}

    # Resume check
    if resume:
        existing = _get_detection_count(conn, camera_uuid)
        if existing > 0:
            log.info("%s --resume: %d detections already exist, skipping.", prefix, existing)
            conn.close()
            return {"camera_id": cam_external_id, "skipped": True, "reason": "already_indexed", "existing": existing}

    t0 = time.monotonic()
    frame_count = 0
    detection_count = 0
    track_count = 0
    alert_count = 0

    # Override frame_skip for batch mode -- much more aggressive sampling
    original_skip = settings.frame_skip
    settings.__dict__["frame_skip"] = BATCH_FRAME_SKIP

    try:
        aggregator = TrackAggregator(
            camera_uuid,
            keep_best=settings.track_keep_best,
            idle_frames=settings.track_idle_frames,
        )
        anpr_min_width = settings.min_plate_width_px * 4

        with CameraStream(cam_external_id, cookie) as stream:
            for f in stream.frames(
                start_time=start_time,
                end_time=end_time,
            ):
                detections = pipeline.process_frame(
                    f.image, camera_uuid, f.pts_ms, f.archive_ms
                )
                frame_count += 1

                for det in detections:
                    write_detection(conn, det)
                    detection_count += 1

                    if det.track_id is None:
                        continue

                    crop = f.image[
                        det.bbox["y1"]:det.bbox["y2"],
                        det.bbox["x1"]:det.bbox["x2"],
                    ]
                    aggregator.add(
                        track_id=det.track_id,
                        class_name=det.class_name,
                        crop_bgr=crop,
                        bbox=det.bbox,
                        conf=det.confidence,
                        archive_ms=f.archive_ms,
                        frame_idx=f.frame_idx,
                        colour=det.dominant_color,
                    )

                    state = aggregator.tracks.get(det.track_id)
                    if (
                        det.class_name in VEHICLE_CLASS_NAMES
                        and crop.shape[1] >= anpr_min_width
                        and state is not None
                        and len(state.plate_reads) < settings.max_plate_reads_per_track
                    ):
                        text, _conf, char_probs = pipeline.read_plate(crop)
                        if text:
                            aggregator.add_plate_read(det.track_id, text, char_probs)

                # At frame_skip=150 a vehicle is seen once or twice, so tracks
                # close almost immediately. That is expected for a sampling
                # index: it records that a vehicle was here, not its path.
                for finished in aggregator.reap(f.frame_idx):
                    if finalise_track(conn, pipeline, finished, cam_external_id, watchlist):
                        alert_count += 1
                    track_count += 1

                if detections:
                    commit(conn)

                # Progress log every 50 sampled frames
                if frame_count % 50 == 0:
                    elapsed = time.monotonic() - t0
                    log.info(
                        "%s  %d frames sampled | %d vehicles | %.0fs elapsed",
                        prefix, frame_count, track_count, elapsed,
                    )

        for finished in aggregator.drain():
            if finalise_track(conn, pipeline, finished, cam_external_id, watchlist):
                alert_count += 1
            track_count += 1
        commit(conn)

    except StopIteration:
        pass  # stream.frames() exhausted the archive -- normal end
    except Exception as exc:
        log.error("%s Error during indexing: %s", prefix, exc)
        conn.close()
        return {
            "camera_id": cam_external_id,
            "skipped": False,
            "frames_sampled": frame_count,
            "detections_written": detection_count,
            "alerts_fired": alert_count,
            "duration_s": round(time.monotonic() - t0, 1),
            "error": str(exc),
        }
    finally:
        # Restore original frame_skip
        settings.__dict__["frame_skip"] = original_skip

    duration = round(time.monotonic() - t0, 1)
    conn.close()

    log.info(
        "%s -- Done  frames=%d  detections=%d  alerts=%d  time=%.0fs",
        prefix, frame_count, detection_count, alert_count, duration,
    )
    return {
        "camera_id": cam_external_id,
        "skipped": False,
        "frames_sampled": frame_count,
        "detections_written": detection_count,
        "alerts_fired": alert_count,
        "duration_s": duration,
    }


def main() -> None:
    # Declared up front: Python requires `global` before the name is *read*, and
    # it is read below as an argparse default. Declaring it after that read is
    # a SyntaxError, so this module could not be imported at all.
    global BATCH_FRAME_SKIP

    parser = argparse.ArgumentParser(description="Setu batch offline indexer")
    parser.add_argument(
        "--cameras",
        default="all",
        help='Comma-separated camera IDs, or "all" (default: all 30)',
    )
    parser.add_argument(
        "--start-time",
        default=None,
        metavar="HH:MM",
        help="Start position in the 12-hour archive (e.g. 06:00)",
    )
    parser.add_argument(
        "--end-time",
        default=None,
        metavar="HH:MM",
        help="End position in the 12-hour archive (e.g. 22:00)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip cameras that already have detections in the DB",
    )
    parser.add_argument(
        "--frame-skip",
        type=int,
        default=BATCH_FRAME_SKIP,
        help=f"Sample every Nth frame (default: {BATCH_FRAME_SKIP})",
    )
    args = parser.parse_args()
    BATCH_FRAME_SKIP = args.frame_skip

    if args.cameras.lower() == "all":
        cam_ids = [f"cam{i:02d}" for i in range(1, 31)]
    else:
        cam_ids = [c.strip() for c in args.cameras.split(",") if c.strip()]

    log.info("=" * 60)
    log.info("Setu batch indexer starting")
    log.info("Cameras: %s (%d total)", args.cameras, len(cam_ids))
    log.info("Frame skip: every %dth frame (approx 1 frame / %.1fs at 30 FPS)",
             BATCH_FRAME_SKIP, BATCH_FRAME_SKIP / 30.0)
    if args.start_time or args.end_time:
        log.info("Time window: %s -> %s", args.start_time or "00:00", args.end_time or "12:00")
    if args.resume:
        log.info("--resume enabled: cameras with existing detections will be skipped")
    log.info("=" * 60)

    log.info("Warming AI models...")
    warm_all_models()
    log.info("Models ready")

    cookie = get_cookie()

    run_start = time.monotonic()
    results = []

    for idx, cam_id in enumerate(cam_ids, 1):
        log.info("")
        log.info("-" * 40)
        log.info("Camera %d / %d -- %s", idx, len(cam_ids), cam_id)
        log.info("-" * 40)

        result = index_camera(
            cam_id,
            cookie,
            start_time=args.start_time,
            end_time=args.end_time,
            resume=args.resume,
        )
        results.append(result)

    total_elapsed = round(time.monotonic() - run_start, 1)
    indexed = [r for r in results if not r.get("skipped")]
    skipped = [r for r in results if r.get("skipped")]
    errored = [r for r in indexed if "error" in r]

    total_frames = sum(r.get("frames_sampled", 0) for r in indexed)
    total_dets   = sum(r.get("detections_written", 0) for r in indexed)
    total_alerts = sum(r.get("alerts_fired", 0) for r in indexed)

    log.info("")
    log.info("=" * 60)
    log.info("BATCH COMPLETE")
    log.info("  Total time:     %.0f s (%.1f min)", total_elapsed, total_elapsed / 60)
    log.info("  Cameras:        %d indexed | %d skipped | %d errors",
             len(indexed), len(skipped), len(errored))
    log.info("  Frames sampled: %d", total_frames)
    log.info("  Detections:     %d", total_dets)
    log.info("  Alerts fired:   %d", total_alerts)

    if errored:
        log.warning("Cameras with errors (re-run with --resume to retry):")
        for r in errored:
            log.warning("  %s -- %s", r["camera_id"], r.get("error", "unknown"))

    if skipped:
        log.info("Skipped cameras:")
        for r in skipped:
            log.info("  %s (%s)", r["camera_id"], r.get("reason", ""))

    log.info("=" * 60)

    if errored:
        sys.exit(1)


if __name__ == "__main__":
    main()
