"""Setu ml-worker — multi-camera orchestration.

Entry point: python -m ml.worker  (or: setu-worker)

Launches one thread per configured camera. Each thread runs an independent
reconnect loop so a single dead feed never stalls the others.

Startup sequence:
  1. Warm all AI models (so the first frame isn't slow)
  2. Apply DB schema + seed cameras (idempotent)
  3. Start per-camera threads
  4. Block on thread join (Ctrl-C to stop)
"""

import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone

from ml.aggregate import TrackAggregator, finalise_plate
from ml.config import settings
from ml.db import (
    commit,
    get_camera_id,
    get_connection,
    write_alert,
    write_detection,
    write_track,
)
from ml.models import warm_all_models
from ml.pipeline import DetectionPipeline
from ml.storage import upload_thumbnail
from ml.stream import CameraStream
from ml.watchlist import WatchlistChecker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_stop_event = threading.Event()


VEHICLE_CLASS_NAMES = {"car", "motorcycle", "bus", "truck"}


def finalise_track(conn, pipeline, track, cam_external_id, watchlist) -> bool:
    """Turn a finished track into one vehicle_tracks row, and alert on it.

    This is where the expensive models finally run — once per vehicle, on the
    few sharpest crops of it, rather than once per box per frame.

    Returns True if the track raised a watchlist alert.
    """
    crops = [b.image for b in track.best]
    if not crops:
        return False

    # The best crop represents the vehicle; the rest exist to give ANPR and the
    # quality ranking something to choose between.
    embeddings = pipeline.embed_batch(crops)
    embedding = embeddings[0] if embeddings else None

    plate = finalise_plate(track.plate_reads)
    thumbnail_key = upload_thumbnail(
        crops[0], f"{track.camera_id}-{track.track_id}", cam_external_id,
        datetime.now(timezone.utc),
    )

    track_row_id = write_track(conn, track, embedding, plate, thumbnail_key)

    plate_text, plate_status = plate[0], plate[1]
    # Only an `exact` plate may raise an alert. A corrected one is a guess, and
    # a guess that names a real registration is worse than no plate at all.
    hit = watchlist.check_values(
        plate_text if plate_status == "exact" else None, embedding
    )
    if hit:
        write_alert(conn, hit, vehicle_track_id=track_row_id)
        log.warning(
            "[%s] WATCHLIST MATCH — track=%s plate=%s (%s, %d votes)",
            cam_external_id, track.track_id, plate_text, plate_status, plate[3],
        )
    return bool(hit)


def process_camera(cam_external_id: str) -> None:
    """Run the full inference loop for one camera until _stop_event is set."""
    prefix = f"[{cam_external_id}]"
    log.info("%s Starting camera worker", prefix)

    # Each thread gets its own DB connection and pipeline instance.
    # DetectionPipeline re-uses shared model singletons (thread-safe for inference).
    conn = get_connection()
    pipeline = DetectionPipeline()
    watchlist = WatchlistChecker(conn)
    watchlist.refresh()

    camera_uuid = get_camera_id(conn, cam_external_id)
    if camera_uuid is None:
        log.error("%s Camera not found in DB — run scripts/seed.py first", prefix)
        return

    from ml.auth import get_cookie
    cookie = get_cookie()

    aggregator = TrackAggregator(
        camera_uuid,
        keep_best=settings.track_keep_best,
        idle_frames=settings.track_idle_frames,
    )
    frame_count = 0
    track_count = 0
    anpr_min_width = settings.min_plate_width_px * 4

    with CameraStream(cam_external_id, cookie) as stream:
        for f in stream.frames():
            if _stop_event.is_set():
                break

            if f.frame_idx % 500 == 0 and f.frame_idx > 0:
                watchlist.refresh()

            detections = pipeline.process_frame(
                f.image, camera_uuid, f.pts_ms, f.archive_ms
            )
            frame_count += 1

            for det in detections:
                # Trajectory row: cheap, no embedding, no plate. Those belong to
                # the track, which knows more than any single frame does.
                write_detection(conn, det)

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

                # Read a plate only when the vehicle is physically close enough
                # for one to exist in the pixels. A plate is roughly a quarter of
                # the vehicle's width, so below 4x the OCR floor there is nothing
                # to find and the read would only add noise to the vote.
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

            for finished in aggregator.reap(f.frame_idx):
                finalise_track(conn, pipeline, finished, cam_external_id, watchlist)
                track_count += 1

            if detections:
                commit(conn)

            if frame_count % 100 == 0:
                log.info(
                    "%s %d frames | %d vehicles recorded | %d tracks open",
                    prefix, frame_count, track_count, len(aggregator),
                )

    # Vehicles still in view when we stopped are real sightings too.
    for finished in aggregator.drain():
        finalise_track(conn, pipeline, finished, cam_external_id, watchlist)
        track_count += 1
    commit(conn)
    log.info("%s Stopped — %d frames, %d vehicles recorded", prefix, frame_count, track_count)


def main() -> None:
    log.info("═" * 60)
    log.info("Setu ml-worker starting")
    log.info("Cameras: %s", settings.cameras)
    log.info("Frame skip: every %dth frame", settings.frame_skip)
    log.info("═" * 60)

    # Warm all models before spawning threads (avoid race on first load)
    log.info("Loading AI models…")
    warm_all_models()
    log.info("Models ready ✓")

    # Graceful shutdown on Ctrl-C or SIGTERM
    def _handle_signal(sig, frame):
        log.info("Received signal %s — shutting down…", sig)
        _stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    cam_ids = settings.camera_ids()
    log.info("Starting %d camera workers: %s", len(cam_ids), cam_ids)

    threads = []
    for cam_id in cam_ids:
        t = threading.Thread(
            target=process_camera,
            args=(cam_id,),
            name=f"cam-{cam_id}",
            daemon=True,
        )
        threads.append(t)
        t.start()
        time.sleep(0.5)  # stagger starts to avoid GPU init race

    log.info("All camera workers started. Press Ctrl-C to stop.")

    # Wait for stop signal
    _stop_event.wait()

    log.info("Waiting for camera threads to finish…")
    for t in threads:
        t.join(timeout=10)

    log.info("Setu ml-worker stopped.")


if __name__ == "__main__":
    main()
