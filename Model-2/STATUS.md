# Setu ML Pipeline — Status & Session Tracking

**Project:** Gujarat Police Innovation Challenge 2026 — Setu Surveillance Intelligence  
**Role:** ML Engineer (Rudra)  
**Current Date:** September 04, 2026  
**Timeline Target:** Complete ML Layer by **September 05 EOD** -> Backend & Frontend integration by **September 06 midnight**

---

## 1. Executive Summary

The Setu ML worker processes 30 real camera feeds (12-hour HLS loops, 480p, AES-128 encrypted) provided by the hackathon sandbox at `cctv.corp8.cloud`. The primary goal is **Temporal Natural Language Semantic Search** and **Visual Re-identification** across ~360 hours of surveillance footage, reducing hundreds of hours of manual footage review to sub-second vector queries.

---

## 2. Completed Thus Far

### A. Feed Ingestion & Streaming
- [x] **HLS Decryption Engine (`ml/stream.py`)**: Implemented manual AES-128 CBC segment decryption via `pycryptodome` and OpenCV frame decoding.
- [x] **Grey Smear Artifact Resolution**: Identified that RTSP TCP streaming caused severe grey smearing due to missed P-frame references on reconnects. Switching to HLS segments (each starting with an IDR keyframe) completely eliminated this issue.
- [x] **Stream Prefetching**: Double-buffered segment fetcher downloads segment N+1 in the background while segment N is decoding.
- [x] **Transport Note**: `ml/stream.py` uses TCP socket handling and `rtsp_transport;tcp` for the opt-in RTSP fallback (`FORCE_RTSP=true`), while defaulting to HLS which runs over HTTP/TCP for reliable deterministic decoding.

### B. Schema & Data Pipeline Enhancements
- [x] **Video Seek Support (`pts_ms`)**: Added `pts_ms DOUBLE PRECISION` to `Detection` dataclass, `ml/pipeline.py`, `ml/db.py`, and `scripts/schema.sql`. This records `cv2.CAP_PROP_POS_MSEC` so the frontend video player can jump directly to the exact millisecond in the stream.
- [x] **Dominant Color Extraction**: Added HSV histogram bucketing in `ml/pipeline.py` (`_extract_color()`) classifying vehicle crops into `red`, `white`, `black`, `silver`, `blue`, `yellow`, `green`, `orange`, or `unknown`. Persisted in `detections.dominant_color`.
- [x] **ByteTrack Multi-Camera Isolation**: Decoupled tracking from `yolo.track()`. Each camera thread now owns an isolated `BYTETracker` instance (`ultralytics.trackers.BYTETracker`), preventing cross-thread `track_id` bleed. Detections are matched via IoU back to YOLO bounding boxes.
- [x] **OpenCLIP Embeddings**: 512-dimensional L2-normalized embeddings generated via `open_clip` ViT-B/32 (exact `openai` weights) for every vehicle detection, stored in Postgres `vector(512)`.
- [x] **Watchlist Alerts**: Real-time checking against `watchlist_entries` for both exact plate strings and person/vehicle embeddings (`cosine similarity >= 0.75`). Automatically triggers PostgreSQL `pg_notify('setu_alerts', alert_id)`.
- [x] **MinIO Thumbnail Storage**: Bounding box crops uploaded directly to MinIO bucket `setu-clips` under structured keys (`{cam_id}/{date}/{det_id}.jpg`).
- [x] **Documentation & Architecture Sync**: Updated `Docs-repo/docs/ARCHITECTURE.md` to reflect 30 real HLS feeds, isolated ByteTrack, no MediaMTX, and the new schema fields (`pts_ms`, `dominant_color`).

### C. Batch Offline Indexing
- [x] **Batch Script (`scripts/batch_index.py`)**: Built sequential 30-camera batch indexer with `frame_skip=150` (~1 frame per 5 seconds), `--resume` capability to skip already indexed cameras, time-window filtering (`--start-time`, `--end-time`), and registered console command `setu-index`.

---

## 3. What Is Currently Being Done

- [ ] **Fresh Schema Deployment & Docker Execution**:
  - Running database reset (`scripts/reset.py`) and applying updated `scripts/schema.sql` with `pts_ms` and `dominant_color` inside the PostgreSQL container.
  - Seeding the 30 Sentinel cameras with coordinates (`scripts/seed.py`).
- [ ] **End-to-End Pipeline Dry Run**:
  - Verifying a short test run on `cam04` and `cam12` to verify that `track_id`, `pts_ms`, `dominant_color`, `embedding`, and MinIO thumbnails are all populated cleanly.

---

## 4. What Is Left To Do (Critical Before Sep 05 EOD)

### 1. Color Extraction Testing & Calibration
- Test dominant color output across real vehicle crops from day and night footage.
- Verify that common edge cases (silver vs white, shadows, tinted windshields) map to reasonable buckets.
- Ensure the backend query parser can leverage `dominant_color` as a structured SQL filter (e.g., `WHERE dominant_color = 'red'`) to narrow CLIP cosine searches.

### 2. Embeddings Validation & Sanity Testing
- Verify that CLIP text embeddings ("white swift", "red bus", "yellow auto rickshaw", "delivery scooter") produce expected cosine similarity rankings against stored image embeddings in `detections`.
- Confirm that the backend team has the exact matching configuration (`open_clip` ViT-B/32, pretrained `openai`) to prevent vector space divergence.
- Test visual re-identification: verify that crops of the same vehicle across consecutive frames have high similarity (>= 0.80).

### 3. Batch Embedding Test & Full Overnight Run
- **Test Run**: Run `python -m scripts.batch_index --cameras cam04,cam12 --frame-skip 150` on 2 high-traffic cameras to benchmark GPU/CPU throughput (target: ~60-90 seconds per 12h feed at 1 frame/5s) and confirm no memory leaks or stream timeouts.
- **Full Overnight Run**: Execute `python -m scripts.batch_index --start-time 06:00 --end-time 22:00` across all 30 cameras.
- **Verification**: Run post-batch SQL queries to verify detection counts, valid `pts_ms` distribution, non-null embeddings, and HNSW index health.

### 4. Backend & Frontend Handoff Checklist
- Confirm backend can generate presigned MinIO URLs from `thumbnail_key`.
- Confirm backend can translate `pts_ms` to HLS playback seek positions in the web player.
- Confirm WebSocket alert listener receives `pg_notify('setu_alerts')` on watchlist matches.