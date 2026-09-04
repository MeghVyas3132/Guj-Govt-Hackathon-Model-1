# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# Setu ml-worker — GPU inference container
#
# Build:
#   docker build -t setu-ml .
# Run (with GPU):
#   docker run --gpus all --env-file .env --network host setu-ml
# Run (CPU only):
#   docker run --env-file .env --network host setu-ml
# ─────────────────────────────────────────────────────────────────────────────

# CUDA 12.1 + cuDNN 8 on Ubuntu 22.04. Change to cpu-only base if no GPU available:
#   FROM python:3.11-slim
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System dependencies: ffmpeg (for OpenCV HLS/RTSP decoding), libGL (for OpenCV)
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-dev \
        python3-pip \
        ffmpeg \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Make python3.11 the default python
RUN ln -sf /usr/bin/python3.11 /usr/bin/python && \
    ln -sf /usr/bin/python3.11 /usr/bin/python3

WORKDIR /app

# Install PyTorch with CUDA 12.1 first (large, cache it as a separate layer)
RUN pip install --no-cache-dir \
    torch==2.3.1+cu121 \
    torchvision==0.18.1+cu121 \
    --index-url https://download.pytorch.org/whl/cu121

# Install remaining dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

# Pre-download AI models at build time so the container starts instantly.
# Models are cached in the image layer — no download delay at inference time.
RUN python -c "from ultralytics import YOLO; YOLO('yolov8s.pt')" && \
    python -c "import open_clip; open_clip.create_model_and_transforms('ViT-B-32', pretrained='openai')" && \
    python -c "from huggingface_hub import hf_hub_download; hf_hub_download('FarAwayFer/yolov8n_license_plate_recognition', 'best_yolov8n.pt')" && \
    python -c "from fast_plate_ocr import LicensePlateRecognizer; LicensePlateRecognizer('global-plates-mobile-vit-v2-model')"

# Copy source code (after model downloads so code changes don't invalidate the model cache layer)
COPY ml/ ml/
COPY scripts/ scripts/

# Health check: verify the worker process is importable
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import ml.worker" || exit 1

CMD ["python", "-m", "ml.worker"]
