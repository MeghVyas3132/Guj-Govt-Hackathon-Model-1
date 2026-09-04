import io
import logging
import threading
from datetime import datetime
import cv2
import numpy as np
from minio import Minio

from ml.config import settings

log = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()
_bucket_ensured = False

def _get_client() -> Minio:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = Minio(
                    endpoint=settings.minio_endpoint,
                    access_key=settings.minio_access_key,
                    secret_key=settings.minio_secret_key,
                    secure=settings.minio_secure,
                )
    return _client

def upload_thumbnail(crop_bgr: np.ndarray, detection_id: str, camera_external_id: str, ts: datetime) -> str | None:
    if crop_bgr is None or crop_bgr.size == 0:
        return None

    try:
        client = _get_client()
        global _bucket_ensured
        if not _bucket_ensured:
            with _client_lock:
                if not _bucket_ensured:
                    if not client.bucket_exists(settings.minio_bucket):
                        client.make_bucket(settings.minio_bucket)
                    _bucket_ensured = True
                    
        key = f"{camera_external_id}/{ts.strftime('%Y/%m/%d')}/{detection_id}.jpg"
        success, buf = cv2.imencode(".jpg", crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not success:
            return None
            
        data = buf.tobytes()
        client.put_object(
            bucket_name=settings.minio_bucket,
            object_name=key,
            data=io.BytesIO(data),
            length=len(data),
            content_type="image/jpeg",
        )
        return key
    except Exception as e:
        log.warning(f"MinIO upload failed: {e}")
        return None
