import threading
import logging
import torch

from ml.config import settings

log = logging.getLogger(__name__)

VEHICLE_CLASSES = {0: 'person', 2: 'car', 3: 'motorcycle', 5: 'bus', 7: 'truck'}
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

_yolo = None
_yolo_lock = threading.Lock()

_clip = None
_clip_preprocess = None
_clip_lock = threading.Lock()

_plate_detector = None
_plate_lock = threading.Lock()

_plate_ocr = None
_ocr_lock = threading.Lock()

def get_yolo():
    global _yolo
    with _yolo_lock:
        if _yolo is None:
            from ultralytics import YOLO
            log.info(f"Loading YOLO model {settings.yolo_model}")
            _yolo = YOLO(settings.yolo_model).to(device)
    return _yolo

def get_clip():
    global _clip, _clip_preprocess
    with _clip_lock:
        if _clip is None:
            import open_clip
            log.info(f"Loading CLIP model {settings.clip_model}")
            _clip, _, _clip_preprocess = open_clip.create_model_and_transforms(
                settings.clip_model, pretrained=settings.clip_pretrained
            )
            _clip = _clip.to(device).eval()
    return _clip, _clip_preprocess

def get_plate_detector():
    global _plate_detector
    with _plate_lock:
        if _plate_detector is None:
            from ultralytics import YOLO
            import os
            
            model_path = settings.plate_model
            if "/" in model_path and not os.path.exists(model_path):
                from huggingface_hub import hf_hub_download
                log.info(f"Downloading plate detector from HF Hub: {model_path}")
                # We expect format 'repo_id/filename' or just 'repo_id' with 'best.pt'
                parts = model_path.split("/")
                repo_id = "/".join(parts[:2])
                filename = parts[2] if len(parts) > 2 else "best_yolov8n.pt" if repo_id == "FarAwayFer/yolov8n_license_plate_recognition" else "best.pt"
                model_path = hf_hub_download(repo_id, filename)
                
            log.info(f"Loading plate detector {model_path}")
            _plate_detector = YOLO(model_path).to(device)
    return _plate_detector

def get_plate_ocr():
    global _plate_ocr
    with _ocr_lock:
        if _plate_ocr is None:
            from fast_plate_ocr import LicensePlateRecognizer
            log.info(f"Loading plate OCR {settings.plate_ocr_model}")
            _plate_ocr = LicensePlateRecognizer(settings.plate_ocr_model)
    return _plate_ocr

def warm_all_models():
    get_yolo()
    get_clip()
    get_plate_detector()
    get_plate_ocr()
