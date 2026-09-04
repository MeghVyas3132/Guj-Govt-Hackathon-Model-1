import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import psycopg2
from pgvector.psycopg2 import register_vector
from psycopg2.extras import RealDictCursor

from ml.config import settings
from ml.pipeline import Detection

log = logging.getLogger(__name__)

def get_connection():
    conn = psycopg2.connect(settings.database_url, cursor_factory=RealDictCursor)
    register_vector(conn)
    return conn

def get_camera_id(conn, external_id: str) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM cameras WHERE external_id = %s", (external_id,))
        row = cur.fetchone()
        return str(row['id']) if row else None

def write_detection(conn, det: Detection) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO detections 
            (id, camera_id, track_id, class, bbox, confidence, plate_text, plate_confidence, embedding, thumbnail_key, ts, pts_ms, archive_ms, dominant_color)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (
            det.id, det.camera_id, det.track_id, det.class_name, json.dumps(det.bbox), 
            det.confidence, det.plate_text, det.plate_confidence, 
            det.embedding, det.thumbnail_key, det.ts, det.pts_ms, det.archive_ms,
            det.dominant_color,
        ))

def write_track(conn, track, embedding, plate, thumbnail_key: Optional[str]) -> str:
    """Persist one finished vehicle sighting and return its row id.

    Args:
        track: a ml.aggregate.TrackState that has left the scene.
        embedding: 512-d vector from the track's sharpest crop, or None.
        plate: (text, status, agreement, votes) from ml.aggregate.finalise_plate.
        thumbnail_key: MinIO key of the best crop.
    """
    plate_text, plate_status, plate_agreement, plate_votes = plate
    row_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO vehicle_tracks
            (id, camera_id, track_id, class, dominant_color, first_ms, last_ms,
             n_observations, embedding, plate_text, plate_status, plate_agreement,
             plate_votes, thumbnail_key, trajectory, ts)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (
            row_id, track.camera_id, track.track_id, track.class_name,
            track.dominant_colour, track.first_ms, track.last_ms,
            track.n_observations, embedding, plate_text, plate_status,
            plate_agreement, plate_votes, thumbnail_key,
            json.dumps(track.trajectory), datetime.now(timezone.utc),
        ))
    return row_id


def write_alert(
    conn,
    watchlist_entry_id: str,
    detection_id: Optional[str] = None,
    vehicle_track_id: Optional[str] = None,
) -> None:
    """Raise an alert against a finished track, or a single detection."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO alerts (watchlist_entry_id, detection_id, vehicle_track_id, status)
            VALUES (%s, %s, %s, 'pending')
        """, (watchlist_entry_id, detection_id, vehicle_track_id))

def commit(conn):
    try:
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error(f"Failed to commit: {e}")
        raise
