# Setu — ML Worker

AI inference pipeline for cross-camera vehicle tracking, ANPR, and natural-language search.
Part of the Setu (Model 2/3) AI surveillance layer for the Gujarat Police Innovation Challenge 2026.

## What it does

Connects to the Sentinel CCTV sandbox (30 real Gujarat camera feeds), runs AI inference on each frame, and writes structured data to PostgreSQL + MinIO:

| Step | Model | Output |
|---|---|---|
| Detection | YOLOv8s (COCO) | bounding boxes per frame |
| Tracking | ByteTrack (isolated per-camera instance) | consistent `track_id` across frames |
| Embedding | open_clip ViT-B/32 (OpenAI weights) | 512-dim L2-normalized vector per detection |
| Color Classification | HSV histogram bucketing | `dominant_color` (red, white, black, silver, blue, yellow, green, orange, unknown) |
| Frame Seek Offset | CAP_PROP_POS_MSEC | `pts_ms` in-segment offset (ms) for video player seeking |
| ANPR | yolov8n-license-plate + fast-plate-ocr | `plate_text` or `null` (best-effort) |
| Persistence | psycopg2 + pgvector | `detections` rows in Postgres |
| Thumbnails | minio | JPEG crops in `setu-clips` bucket |
| Alerts | SQL | `alerts` row + `pg_notify` when watchlist matches |

## Prerequisites

- Python 3.11+
- NVIDIA GPU with CUDA 12.x (RTX 3060 or better) — CPU fallback works but is slow
- Docker (for Postgres + MinIO)
- Access to the Sentinel camera sandbox — set `SENTINEL_PASSWORD` in `.env` (never commit it)

## Setup

```bash
cd ml/

# 1. Create virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

# 2. Install PyTorch with CUDA first (large download, do this once)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# 3. Install remaining dependencies
pip install -e .

# 4. Copy env file
cp .env.example .env
# Edit .env if needed (defaults work for local dev)

# 5. Start local Postgres (pgvector) + MinIO
# Standalone container:
docker run -d --name setu-pg -p 5432:5432 \
  -e POSTGRES_USER=setu -e POSTGRES_PASSWORD=setu_dev_only -e POSTGRES_DB=setu \
  pgvector/pgvector:pg16

docker run -d --name setu-minio -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=setu -e MINIO_ROOT_PASSWORD=setu_dev_only \
  minio/minio server /data --console-address ":9001"

# Or using docker compose:
# docker compose up -d setu-pg setu-minio

# 6. Apply DB schema (Execute .sql in Docker container)
# PowerShell (Windows):
Get-Content scripts\schema.sql | docker exec -i setu-pg psql -U setu -d setu

# Linux / macOS / Git Bash:
docker exec -i setu-pg psql -U setu -d setu < scripts/schema.sql

# If using docker compose:
# Get-Content scripts\schema.sql | docker compose exec -T setu-pg psql -U setu -d setu
# docker compose exec -T setu-pg psql -U setu -d setu < scripts/schema.sql

# If native psql client is installed:
# psql postgresql://setu:setu_dev_only@localhost:5432/setu -f scripts/schema.sql

# 7. Seed cameras + watchlist
python -m scripts.seed

# 8. (Optional) Check which feeds are reachable
python -m scripts.check_feeds
```

## Run

### Option A: Offline Batch Indexer (Recommended for Hackathon Demo Data)

Processes cameras offline to populate embeddings, video seek timestamps (`pts_ms`), dominant colors, and thumbnails:

```bash
# Run daytime hours (06:00 to 22:00) across all 30 cameras:
python -m scripts.batch_index --start-time 06:00 --end-time 22:00

# Resume after partial run (skips cameras that already have detections):
python -m scripts.batch_index --resume

# Run a specific camera subset:
python -m scripts.batch_index --cameras cam04,cam12

# Using console entry point:
setu-index --resume
```

### Option B: Live Multi-Camera Worker

Runs live/looping streams concurrently in separate threads:

```bash
# Start the multi-camera worker
python -m ml.worker

# Or using console entry point:
setu-worker
```

Set `CAMERAS=cam04,cam05,cam06` in `.env` to choose which cameras to process.  
Set `CAMERAS=all` to process all 30 (GPU required).

### Database Reset & Re-schema

To wipe existing test detections and apply a clean schema:

```bash
# 1. Truncate detections, alerts, and MinIO thumbnails
python -m scripts.reset

# 2. Re-apply schema in docker container
Get-Content scripts\schema.sql | docker exec -i setu-pg psql -U setu -d setu

# 3. Re-seed cameras and watchlist
python -m scripts.seed
```

## Docker

```bash
# Build (pre-downloads all AI model weights into the image)
docker build -t setu-ml .

# Run with GPU
docker run --gpus all --env-file .env --network host setu-ml
```

## Architecture notes

- **Stream Transport & TCP Note**: `ml/stream.py` uses TCP socket transport (`rtsp_transport;tcp` for OpenCV RTSP, and standard HTTP/TCP for HLS segment fetching) to avoid UDP packet loss. **Note on the old TCP method:** Naive RTSP streaming over TCP suffered from severe grey smear artifacts during reconnects and stream joining due to missing reference frames (P-frames arriving before IDR keyframes). To guarantee zero grey smear, HLS (where each segment begins with a standalone IDR keyframe) is the primary ingestion mechanism. Authenticated RTSP over TCP is retained as an opt-in fallback via `FORCE_RTSP=true` in `.env`.
- **Frame subsampling**: Controlled by `FRAME_SKIP`. The live worker defaults to `5` (process every 5th frame ≈ 6 FPS). The batch indexer uses `150` (sample 1 frame every 5s at 30 FPS source) to make overnight offline indexing of 360 hours of footage feasible on local hardware.
- **CLIP checkpoint**: `ViT-B-32` / `openai` weights — **the backend must use the identical checkpoint** for NL search text encoding. Different weights = broken cosine similarity.
- **Dominant color extraction**: HSV histogram bucketing on vehicle crops classifies colors into `red`, `white`, `black`, `silver`, `blue`, `yellow`, `green`, `orange`, `unknown`, enabling structured SQL filtering alongside vector searches.
- **Video Seek (`pts_ms`)**: Every detection row includes `pts_ms` (`cv2.CAP_PROP_POS_MSEC`), allowing frontend video players to seek directly to the moment of detection.
- **ANPR is best-effort**: Plates are null when unreadable due to 480p resolution. Visual re-ID via CLIP embeddings is the primary matching mechanism.
- **Watchlist alert**: Plate match (exact string) or embedding match (cosine similarity ≥ 0.75). On match: row inserted into `alerts` + `pg_notify('setu_alerts', alert_id)` fires automatically via DB trigger.

## Database schema

See [`scripts/schema.sql`](scripts/schema.sql). Key tables:

| Table | Description |
|---|---|
| `cameras` | Sentinel sandbox cameras with lat/lng |
| `detections` | One row per detected object per frame; `embedding vector(512)` for HNSW search |
| `watchlist_entries` | Plates or reference embeddings to watch for |
| `alerts` | Pending/confirmed/dismissed watchlist matches |

## Integration with Model 1 (Sentinel CCTV Registry)

Model 1 provides the camera registry. In production, the ML worker queries
`GET /api/v1/cameras/{id}/streams` on Model 1's FastAPI for stream URLs.
For the hackathon build, we connect directly to the Sentinel sandbox
(`cctv.corp8.cloud`) which Model 1 also uses as its upstream source.

## Team handoff (for backend developer)

The backend FastAPI (Setu) needs to:
1. Connect to the same Postgres + pgvector database this worker writes to
2. Load `open_clip.create_model_and_transforms('ViT-B-32', pretrained='openai')` for NL search text encoding
3. Listen for `pg_notify('setu_alerts', ...)` on a dedicated asyncpg connection and push to WebSocket
4. Serve `thumbnail_key` values as presigned MinIO URLs (never raw keys)
