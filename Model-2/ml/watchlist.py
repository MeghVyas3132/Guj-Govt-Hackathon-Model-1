import threading
import logging
import numpy as np

from ml.config import settings
from ml.pipeline import Detection

log = logging.getLogger(__name__)

class WatchlistChecker:
    def __init__(self, conn):
        self.conn = conn
        self._entries = []
        self._lock = threading.Lock()
        
    def refresh(self):
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT id, type, value, reference_embedding FROM watchlist_entries")
                rows = cur.fetchall()
                
                parsed = []
                for r in rows:
                    if r['type'] == 'person_embedding' and r['reference_embedding']:
                        r['reference_embedding'] = np.array(r['reference_embedding'], dtype=np.float32)
                    parsed.append(r)
                    
                with self._lock:
                    self._entries = parsed
                    
        except Exception as e:
            log.warning(f"Failed to refresh watchlist: {e}")

    def check(self, det: Detection) -> str | None:
        """Check a single detection. Kept for the per-frame diagnostic scripts."""
        return self.check_values(det.plate_text, det.embedding)

    def check_values(
        self, plate_text: str | None, embedding: list[float] | None
    ) -> str | None:
        """Check a plate and/or an embedding against the watchlist.

        The worker calls this once per finished track rather than once per frame.
        That matters beyond cost: alerting per frame raised the same alert dozens
        of times for one vehicle, and it decided on evidence from a single frame
        when the track had far more to offer.
        """
        with self._lock:
            entries = self._entries.copy()

        det_emb = None
        if embedding is not None:
            det_emb = np.array(embedding, dtype=np.float32)

        for entry in entries:
            if entry['type'] == 'plate' and plate_text and entry['value']:
                if plate_text.upper() == entry['value'].upper():
                    return str(entry['id'])

            elif (
                entry['type'] == 'person_embedding'
                and entry['reference_embedding'] is not None
                and det_emb is not None
            ):
                sim = float(np.dot(det_emb, entry['reference_embedding']))
                if sim >= settings.watchlist_embed_threshold:
                    return str(entry['id'])

        return None
