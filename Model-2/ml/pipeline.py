import uuid
import logging
from dataclasses import dataclass
from typing import Optional
import cv2
from datetime import datetime, timezone
from PIL import Image
import torch
import numpy as np
import re

from ml.config import settings
from ml.models import get_yolo, get_clip, get_plate_detector, get_plate_ocr, VEHICLE_CLASSES, device

log = logging.getLogger(__name__)

import threading

_inference_lock = threading.RLock()

@dataclass
class Detection:
    id: str
    camera_id: str
    track_id: Optional[int]
    class_name: str
    bbox: dict
    confidence: float
    plate_text: Optional[str]
    plate_confidence: Optional[float]
    embedding: Optional[list[float]]   # None on the per-track path; set on vehicle_tracks
    thumbnail_key: Optional[str]
    ts: datetime            # wall-clock at ingest — telemetry, not a video timeline
    pts_ms: float           # position-in-segment in milliseconds (from CAP_PROP_POS_MSEC)
    archive_ms: float       # position in the 12-hour archive — the orderable timeline
    dominant_color: Optional[str]  # HSV colour bucket for NL search precision

class DetectionPipeline:
    def __init__(self):
        self.yolo = get_yolo()
        self.clip, self.clip_preprocess = get_clip()
        self.plate_detector = get_plate_detector()
        self.plate_ocr = get_plate_ocr()

        # One isolated ByteTracker per DetectionPipeline instance.
        # worker.py creates one pipeline per camera thread, so this is effectively
        # one tracker per camera — no shared state, no cross-camera track_id bleed.
        from ultralytics.trackers import BYTETracker
        from ultralytics.utils import IterableSimpleNamespace
        _tracker_args = IterableSimpleNamespace(
            track_high_thresh=0.5,    # min conf to enter high-conf association
            track_low_thresh=0.1,     # min conf for low-conf second-pass association
            new_track_thresh=0.6,     # min conf to create a new track
            track_buffer=30,          # frames to keep a lost track (30 ≈ 5s at 6 FPS)
            match_thresh=0.8,         # IoU threshold for first-pass association
            fuse_score=True,          # fuse detection score into track score
            frame_rate=6,             # approximate processed FPS (frame_skip=5 at 30 FPS)
        )
        self.tracker = BYTETracker(_tracker_args)

    def process_frame(
        self,
        frame_bgr: np.ndarray,
        camera_id: str,
        pts_ms: float,
        archive_ms: Optional[float] = None,
        *,
        read_plates: bool = False,
        embed: bool = False,
    ) -> list[Detection]:
        """Detect and track one frame.

        By default this does the cheap work only — detect, track, bucket colour
        — and leaves CLIP and the plate reader alone.  Those run once per
        *vehicle* in ml.aggregate, not once per box per frame, which is both an
        order of magnitude less compute and more accurate, because the
        aggregator gets to choose which frames to spend them on.

        ``read_plates`` and ``embed`` force the old per-frame behaviour.  Only
        scripts/live_view.py wants that, for eyeballing raw model output.
        """
        with _inference_lock:
            results = self.yolo.predict(
                frame_bgr,
                classes=list(VEHICLE_CLASSES.keys()),
                conf=settings.detection_conf_threshold,
                imgsz=settings.yolo_imgsz,
                verbose=False,
            )

        detections: list[Detection] = []
        if not results or not results[0].boxes:
            return detections

        yolo_result = results[0]
        boxes = yolo_result.boxes
        ts = datetime.now(timezone.utc)

        # ── ByteTrack ────────────────────────────────────────────────────────
        # update() reads .conf/.xywh/.cls, which live on Boxes rather than on
        # Results — handing it the Results object raises AttributeError, and
        # since the failure is caught below it presents as "tracking silently
        # does nothing" rather than as an error.
        #
        # The returned rows are [x1, y1, x2, y2, track_id, score, cls, idx], so
        # the last column already maps each row back to its detection. Matching
        # by IoU instead would be both slower and wrong in dense traffic, where
        # the nearest box is often the neighbouring vehicle.
        track_id_by_idx: dict[int, int] = {}
        try:
            tracked = self.tracker.update(boxes.cpu().numpy(), frame_bgr)
            for row in tracked:
                if len(row) >= 8:
                    track_id_by_idx[int(row[-1])] = int(row[4])
        except Exception as exc:
            log.warning("ByteTrack update failed: %s — no track ids this frame", exc)

        h, w = frame_bgr.shape[:2]
        for i, box in enumerate(boxes):
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            conf = float(box.conf[0])
            cls_id = int(box.cls[0])
            track_id = track_id_by_idx.get(i)

            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)

            crop = frame_bgr[y1:y2, x1:x2]
            if crop.size == 0 or crop.shape[0] < 10 or crop.shape[1] < 10:
                continue

            embedding = self._embed_crop(crop) if embed else None
            dominant_color = self._extract_color(crop)

            plate_text, plate_conf = None, None
            if read_plates and cls_id in (2, 3, 5, 7):
                plate_text, plate_conf, _ = self.read_plate(crop)

            detections.append(Detection(
                id=str(uuid.uuid4()),
                camera_id=camera_id,
                track_id=track_id,
                class_name=VEHICLE_CLASSES.get(cls_id, 'unknown'),
                bbox={'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2},
                confidence=conf,
                plate_text=plate_text,
                plate_confidence=plate_conf,
                embedding=embedding,
                thumbnail_key=None,
                ts=ts,
                pts_ms=pts_ms,
                archive_ms=pts_ms if archive_ms is None else archive_ms,
                dominant_color=dominant_color,
            ))

        return detections

    def embed_batch(self, crops: list[np.ndarray]) -> list[list[float]]:
        """Embed several crops in one forward pass.

        Batching matters here: a track contributes a handful of crops and the
        GPU is just as happy taking them together, where one call per crop pays
        the launch overhead every time.
        """
        if not crops:
            return []
        tensors = []
        for crop in crops:
            try:
                pil = Image.fromarray(crop[:, :, ::-1])
                tensors.append(self.clip_preprocess(pil))
            except Exception:
                continue
        if not tensors:
            return []
        batch = torch.stack(tensors).to(device)
        with _inference_lock, torch.no_grad():
            embeds = self.clip.encode_image(batch)
            embeds /= embeds.norm(dim=-1, keepdim=True)
        return embeds.cpu().tolist()

    def _embed_crop(self, crop_bgr: np.ndarray) -> list[float]:
        try:
            crop_rgb = crop_bgr[:, :, ::-1]
            pil_img = Image.fromarray(crop_rgb)
            img_tensor = self.clip_preprocess(pil_img).unsqueeze(0).to(device)
            with torch.no_grad():
                embed = self.clip.encode_image(img_tensor)
                embed /= embed.norm(dim=-1, keepdim=True)
            return embed[0].cpu().tolist()
        except Exception:
            return []

    def _extract_color(self, crop_bgr: np.ndarray) -> Optional[str]:
        """Dominant colour of the crop using HSV histogram bucketing.

        Returns a colour name string suitable for NL search filtering:
        red | white | black | silver | blue | yellow | green | orange | unknown
        """
        try:
            hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
            h = hsv[:, :, 0].astype(np.float32)  # 0–179 in OpenCV
            s = hsv[:, :, 1].astype(np.float32)  # 0–255
            v = hsv[:, :, 2].astype(np.float32)  # 0–255

            total = h.size
            if total == 0:
                return "unknown"

            # ── Achromatic buckets (low saturation) ───────────────────────
            low_s_mask = s < 50   # unsaturated pixels
            low_s_frac = low_s_mask.sum() / total

            if low_s_frac > 0.55:
                # Mostly achromatic — classify by brightness
                mean_v = float(v[low_s_mask].mean()) if low_s_mask.any() else float(v.mean())
                if mean_v < 60:
                    return "black"
                elif mean_v < 140:
                    return "silver"
                else:
                    return "white"

            # ── Chromatic: use hue histogram on saturated pixels only ─────
            sat_mask = s >= 50
            if not sat_mask.any():
                return "unknown"

            h_sat = h[sat_mask]

            # OpenCV hue: 0–179 (degrees / 2). Bucket boundaries in that space:
            #   red:    [0,10) ∪ [170,180)
            #   orange: [10,20)
            #   yellow: [20,35)
            #   green:  [35,85)
            #   blue:   [85,130)
            #   (purple/violet skipped — rare in vehicles)
            buckets = {
                "red":    ((h_sat < 10) | (h_sat >= 170)).sum(),
                "orange": ((h_sat >= 10) & (h_sat < 20)).sum(),
                "yellow": ((h_sat >= 20) & (h_sat < 35)).sum(),
                "green":  ((h_sat >= 35) & (h_sat < 85)).sum(),
                "blue":   ((h_sat >= 85) & (h_sat < 130)).sum(),
            }
            dominant = max(buckets, key=lambda k: buckets[k])
            return dominant
        except Exception:
            return "unknown"

    def read_plate(
        self, vehicle_crop: np.ndarray
    ) -> tuple[Optional[str], Optional[float], Optional[list[float]]]:
        """Read one plate from a vehicle crop.

        Returns ``(text, confidence, char_probs)``.  The per-character
        probabilities are the point: ml.aggregate votes with them across every
        frame of a track, which is the only thing that recovers a plate from
        footage at this bitrate.

        Text here is raw OCR output — deliberately not validated or corrected.
        A single frame does not have the evidence to justify correcting a plate;
        that decision belongs to the vote, once all the reads are in.
        """
        try:
            res = self.plate_detector(vehicle_crop, verbose=False, stream=False)
            if not res or not res[0].boxes:
                return None, None, None

            boxes = res[0].boxes
            best_idx = int(boxes.conf.argmax())
            plate_conf = float(boxes.conf[best_idx])
            if plate_conf < settings.plate_conf_threshold:
                return None, None, None

            px1, py1, px2, py2 = map(int, boxes.xyxy[best_idx].tolist())
            plate_crop = vehicle_crop[py1:py2, px1:px2]
            if plate_crop.size == 0:
                return None, None, None

            # Industry guidance for ANPR is 100-150 px of plate width; below
            # roughly 60 there is no glyph detail left to read and the OCR
            # returns confident noise. Rejecting it here keeps that noise out
            # of the vote, where it would outvote the few good reads.
            if plate_crop.shape[1] < settings.min_plate_width_px:
                return None, None, None

            preds = self.plate_ocr.run(
                self._prepare_plate(plate_crop), return_confidence=True
            )
            if not preds:
                return None, None, None

            pred = preds[0]
            clean_text = re.sub(r'[^A-Z0-9]', '', pred.plate.upper())
            if len(clean_text) < 4:
                return None, None, None

            char_probs = (
                [float(c) for c in pred.char_probs]
                if pred.char_probs is not None and len(pred.char_probs) > 0
                else None
            )
            ocr_conf = float(sum(char_probs) / len(char_probs)) if char_probs else 0.0
            return clean_text, min(plate_conf, ocr_conf), char_probs
        except Exception as exc:
            log.warning("Plate extraction failed: %s", exc)
            return None, None, None

    @staticmethod
    def _prepare_plate(plate_bgr: np.ndarray) -> np.ndarray:
        """Upscale, equalise and sharpen a plate crop before OCR.

        Worth knowing: at 0.006 bits per pixel the strongest signal in a crop is
        often block and ringing artefact, and contrast equalisation amplifies
        that along with the glyphs. Whether this chain helps or hurts is an
        empirical question for scripts/compare_ocr.py on daylight segments —
        it has a preprocess flag for exactly this comparison.
        """
        h, w = plate_bgr.shape[:2]
        upscaled = cv2.resize(plate_bgr, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        denoised = cv2.bilateralFilter(enhanced, 9, 75, 75)
        blurred = cv2.GaussianBlur(denoised, (0, 0), 3)
        return cv2.addWeighted(denoised, 1.5, blurred, -0.5, 0)
