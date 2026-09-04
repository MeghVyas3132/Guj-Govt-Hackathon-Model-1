import json
import logging
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from pgvector.psycopg2 import register_vector

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
            (id, camera_id, track_id, class, bbox, confidence, plate_text, plate_confidence, embedding, thumbnail_key, ts, pts_ms, dominant_color)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (
            det.id, det.camera_id, det.track_id, det.class_name, json.dumps(det.bbox), 
            det.confidence, det.plate_text, det.plate_confidence, 
            det.embedding, det.thumbnail_key, det.ts, det.pts_ms, det.dominant_color,
        ))

def write_alert(conn, watchlist_entry_id: str, detection_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO alerts (watchlist_entry_id, detection_id, status)
            VALUES (%s, %s, 'pending')
        """, (watchlist_entry_id, detection_id))

def commit(conn):
    try:
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error(f"Failed to commit: {e}")
        raise
