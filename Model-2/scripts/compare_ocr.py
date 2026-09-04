"""
OCR model comparison script — random segment sampling edition.

Instead of processing consecutive frames, this script:
  1. Fetches the HLS manifest to get the full segment list (~14,690 segs = 12h)
  2. Randomly samples N segments from within a configurable time range
  3. Downloads, decrypts and decodes each sampled segment
  4. Detects license plates in each frame
  5. Runs all available OCR engines side by side
  6. Applies Indian plate regex validation to each result

Usage:
    # 20 random plate detections from across the whole 12-hour archive:
    python -m scripts.compare_ocr cam06 --samples 20

    # 20 random plates from 9am–11am only:
    python -m scripts.compare_ocr cam06 --samples 20 --start 09:00 --end 11:00

    # Save plate crops to disk for manual inspection:
    python -m scripts.compare_ocr cam06 --samples 20 --save-crops
"""

import argparse
import os
import random
import re
import tempfile
import logging
from pathlib import Path

import cv2
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

from ml.models import get_yolo, get_plate_detector, get_plate_ocr, VEHICLE_CLASSES
from ml.auth import get_cookie
from ml.stream import _HLSFetcher, CameraStream
from ml.config import settings
from ml.plate_validator import validate_plate


def preprocess_plate(plate_bgr: np.ndarray) -> np.ndarray:
    h, w = plate_bgr.shape[:2]
    upscaled = cv2.resize(plate_bgr, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    denoised = cv2.bilateralFilter(enhanced, 9, 75, 75)
    blurred = cv2.GaussianBlur(denoised, (0, 0), 3)
    return cv2.addWeighted(denoised, 1.5, blurred, -0.5, 0)


def run_fast_ocr(plate_bgr, ocr_model, preprocess=True):
    img = preprocess_plate(plate_bgr) if preprocess else cv2.cvtColor(plate_bgr, cv2.COLOR_BGR2GRAY)
    preds = ocr_model.run(img, return_confidence=True)
    if not preds:
        return None, 0.0
    p = preds[0]
    text = re.sub(r'[^A-Z0-9]', '', p.plate.upper())
    conf = float(sum(p.char_probs) / len(p.char_probs)) if p.char_probs is not None and len(p.char_probs) > 0 else 0.0
    return (text or None), conf


def run_easyocr(plate_bgr, reader, preprocess=True):
    img = preprocess_plate(plate_bgr) if preprocess else cv2.cvtColor(plate_bgr, cv2.COLOR_BGR2GRAY)
    results = reader.readtext(img, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
    if not results:
        return None, 0.0
    text = re.sub(r'[^A-Z0-9]', '', ''.join(t for _, t, _ in results).upper())
    conf = sum(c for _, _, c in results) / len(results)
    return (text or None), conf


def run_paddle(plate_bgr, reader, preprocess=True):
    img = preprocess_plate(plate_bgr) if preprocess else plate_bgr
    # PaddleOCR logs a lot of debug info to stdout by default, we suppress it during init
    try:
        result = reader.ocr(img, cls=False)
    except Exception:
        return None, 0.0
        
    if not result or not result[0]:
        return None, 0.0
        
    texts, confs = [], []
    for line in result[0]:
        # line format: [[box points], ('text', conf)]
        texts.append(line[1][0])
        confs.append(line[1][1])
        
    text = re.sub(r'[^A-Z0-9]', '', ''.join(texts).upper())
    conf = sum(confs) / len(confs) if confs else 0.0
    return (text or None), conf


def decode_segment(seg_bytes: bytes) -> list[np.ndarray]:
    """Decode a .ts segment into a list of frames."""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".ts")
    frames = []
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(seg_bytes)
        cap = cv2.VideoCapture(tmp_path)
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            frames.append(frame)
        cap.release()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return frames


def main():
    parser = argparse.ArgumentParser(description="Compare OCR engines on randomly sampled plate crops")
    parser.add_argument("camera_id", nargs="?", default="cam06")
    parser.add_argument("--samples", type=int, default=20,
                        help="Number of plate detections to collect (default: 20)")
    parser.add_argument("--start", metavar="HH:MM", default=None,
                        help="Start of time window, e.g. '08:00'")
    parser.add_argument("--end", metavar="HH:MM", default=None,
                        help="End of time window, e.g. '10:00'")
    parser.add_argument("--save-crops", action="store_true",
                        help="Save plate crops (raw + preprocessed) to plate_crops/")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducible segment selection")
    args = parser.parse_args()

    random.seed(args.seed)
    crop_dir = Path("plate_crops")
    if args.save_crops:
        crop_dir.mkdir(exist_ok=True)

    # ── Load models ──
    log.info("Loading models...")
    yolo = get_yolo()
    plate_det = get_plate_detector()
    fast_ocr = get_plate_ocr()

    log.info("Loading EasyOCR...")
    try:
        import easyocr
        easy_reader = easyocr.Reader(['en'], gpu=True, verbose=False)
        has_easy = True
        log.info("EasyOCR ✓")
    except ImportError:
        log.warning("EasyOCR not installed — skipping that column")
        easy_reader = None
        has_easy = False

    log.info("Loading PaddleOCR...")
    try:
        from paddleocr import PaddleOCR
        import logging as paddle_logging
        paddle_logging.getLogger('ppocr').setLevel(paddle_logging.ERROR)
        paddle_reader = PaddleOCR(use_textline_orientation=False, lang='en')
        has_paddle = True
        log.info("PaddleOCR ✓")
    except ImportError:
        log.warning("PaddleOCR not installed — skipping that column")
        paddle_reader = None
        has_paddle = False

    # ── Fetch manifest and pick random segments ──
    cookie = get_cookie()
    fetcher = _HLSFetcher(cookie)

    log.info(f"Fetching manifest for {args.camera_id}...")
    segments, key_url = fetcher.fetch_manifest(args.camera_id)
    total = len(segments)
    log.info(f"Archive: {total} segments (~{total * 12.0 / total:.1f}h  = 12h total)")

    # Convert time args to indices
    def _t2s(t: str) -> int:
        return CameraStream._time_to_seg_idx(t, total)

    idx_start = _t2s(args.start) if args.start else 0
    idx_end   = _t2s(args.end)   if args.end   else total
    idx_end   = min(idx_end, total)

    pool = list(range(idx_start, idx_end))
    if not pool:
        log.error("No segments in specified range!")
        return

    log.info(
        f"Sampling from segments {idx_start}–{idx_end} "
        f"(~{idx_start*12/total:.1f}h – {idx_end*12/total:.1f}h into archive)"
    )

    # Shuffle the pool so we pick truly random segments across the time window
    random.shuffle(pool)

    # ── Main loop: pick segments until we have enough plate detections ──
    plate_count = 0
    seg_tried = 0
    results_table = []

    for seg_idx in pool:
        if plate_count >= args.samples:
            break

        seg_tried += 1
        seg_url = segments[seg_idx]
        archive_time = f"{int(seg_idx * 12 / total):02d}:{int((seg_idx * 12 / total % 1) * 60):02d}"
        log.info(f"[seg {seg_idx}/{total} @ ~{archive_time}] downloading...")

        try:
            seg_bytes = fetcher.fetch_segment(seg_url, key_url)
        except Exception as e:
            log.warning(f"  Failed to download: {e}")
            continue

        frames = decode_segment(seg_bytes)
        if not frames:
            log.warning("  No frames decoded")
            continue

        # Sample a few frames from this segment (not all — too slow)
        frame_indices = sorted(random.sample(range(len(frames)), min(5, len(frames))))

        for fi in frame_indices:
            frame = frames[fi]

            # Detect vehicles
            res = yolo.predict(frame, classes=list(VEHICLE_CLASSES.keys()),
                               conf=settings.detection_conf_threshold, verbose=False)
            if not res or not res[0].boxes:
                continue

            for box in res[0].boxes:
                cls_id = int(box.cls[0])
                if cls_id not in [2, 3, 5, 7]:
                    continue

                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                h_f, w_f = frame.shape[:2]
                vehicle = frame[max(0,y1):min(h_f,y2), max(0,x1):min(w_f,x2)]
                if vehicle.size == 0:
                    continue

                # Detect plate
                pr = plate_det(vehicle, verbose=False, stream=False)
                if not pr or not pr[0].boxes:
                    continue

                pb = pr[0].boxes
                best = int(pb.conf.argmax())
                pconf = float(pb.conf[best])
                if pconf < 0.20:
                    continue

                px1, py1, px2, py2 = map(int, pb.xyxy[best].tolist())
                plate = vehicle[py1:py2, px1:px2]
                if plate.size == 0 or plate.shape[0] < 5 or plate.shape[1] < 10:
                    continue

                plate_count += 1

                raw_filename = f"p{plate_count:04d}_raw.png"
                pre_filename = f"p{plate_count:04d}_pre.png"
                if args.save_crops:
                    cv2.imwrite(str(crop_dir / raw_filename), plate)
                    cv2.imwrite(str(crop_dir / pre_filename), preprocess_plate(plate))

                # ── Run OCR engines ──
                fp_text, fp_conf = run_fast_ocr(plate, fast_ocr, preprocess=False)
                fpp_text, fpp_conf = run_fast_ocr(plate, fast_ocr, preprocess=True)
                easy_text, easy_conf = (run_easyocr(plate, easy_reader) if has_easy else (None, 0.0))
                
                paddle_raw_text, paddle_raw_conf = (run_paddle(plate, paddle_reader, preprocess=False) if has_paddle else (None, 0.0))
                paddle_pre_text, paddle_pre_conf = (run_paddle(plate, paddle_reader, preprocess=True) if has_paddle else (None, 0.0))

                fp_v   = validate_plate(fp_text)   if fp_text   else None
                fpp_v  = validate_plate(fpp_text)  if fpp_text  else None
                easy_v = validate_plate(easy_text) if easy_text else None
                pad_raw_v = validate_plate(paddle_raw_text) if paddle_raw_text else None
                pad_pre_v = validate_plate(paddle_pre_text) if paddle_pre_text else None

                cls_name = VEHICLE_CLASSES.get(cls_id, "?")
                ts = f"~{archive_time} seg={seg_idx}"

                log.info(f"\n── Plate #{plate_count} ({cls_name}, det={pconf:.0%}, {ts}) ──")
                if args.save_crops:
                    log.info(f"  Files saved:  {raw_filename}  |  {pre_filename}")
                log.info(f"  fast-ocr (raw): {str(fp_text):>12s} ({fp_conf:.0%})  validated={fp_v or '✗'}")
                log.info(f"  fast-ocr (pre): {str(fpp_text):>12s} ({fpp_conf:.0%})  validated={fpp_v or '✗'}")
                if has_easy:
                    log.info(f"  EasyOCR  (pre): {str(easy_text):>12s} ({easy_conf:.0%})  validated={easy_v or '✗'}")
                if has_paddle:
                    log.info(f"  Paddle   (raw): {str(paddle_raw_text):>12s} ({paddle_raw_conf:.0%})  validated={pad_raw_v or '✗'}")
                    log.info(f"  Paddle   (pre): {str(paddle_pre_text):>12s} ({paddle_pre_conf:.0%})  validated={pad_pre_v or '✗'}")

                results_table.append({
                    "plate_no": plate_count, "time": archive_time, "cls": cls_name,
                    "fp_raw_v": fp_v,
                    "fp_pre_v": fpp_v,
                    "easy_v": easy_v,
                    "pad_raw_v": pad_raw_v,
                    "pad_pre_v": pad_pre_v,
                })

                if plate_count >= args.samples:
                    break
            if plate_count >= args.samples:
                break

    # ── Summary ──
    log.info(f"\n{'='*70}")
    log.info(f"Processed {seg_tried} random segments, collected {plate_count} plate crops")

    fp_raw_hits  = sum(1 for r in results_table if r["fp_raw_v"])
    fp_pre_hits  = sum(1 for r in results_table if r["fp_pre_v"])
    easy_hits    = sum(1 for r in results_table if r["easy_v"])
    pad_raw_hits = sum(1 for r in results_table if r["pad_raw_v"])
    pad_pre_hits = sum(1 for r in results_table if r["pad_pre_v"])

    if results_table:
        log.info(f"\nValidation pass rate (Indian plate format):")
        log.info(f"  fast-ocr (raw):       {fp_raw_hits}/{plate_count} = {fp_raw_hits/plate_count:.0%}")
        log.info(f"  fast-ocr (pre):       {fp_pre_hits}/{plate_count} = {fp_pre_hits/plate_count:.0%}")
        if has_easy:
            log.info(f"  EasyOCR  (pre):       {easy_hits}/{plate_count} = {easy_hits/plate_count:.0%}")
        if has_paddle:
            log.info(f"  Paddle   (raw):       {pad_raw_hits}/{plate_count} = {pad_raw_hits/plate_count:.0%}")
            log.info(f"  Paddle   (pre):       {pad_pre_hits}/{plate_count} = {pad_pre_hits/plate_count:.0%}")

    if args.save_crops:
        log.info(f"\nCrops saved to {crop_dir.absolute()}")


if __name__ == "__main__":
    main()
