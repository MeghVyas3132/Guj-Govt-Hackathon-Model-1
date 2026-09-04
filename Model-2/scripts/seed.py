"""Seed the Setu database with demo cameras from the Sentinel catalogue.

Run once after applying schema.sql:
    python -m ml.scripts.seed

Seeds:
  - cameras table from cctv.corp8.cloud/cameras.json (with placeholder lat/lng)
  - 1 plate watchlist entry (update the plate value once you identify one in the feeds)
  - 1 embedding watchlist entry slot (filled manually later if needed)
"""

import json
import os
import logging
import sys
import uuid
from datetime import datetime, timezone

import psycopg2
import requests

# Real Ahmedabad/Gandhinagar junction coordinates for the demo cameras.
# These are manually assigned — the video content doesn't need to visually match
# the location; judges understand it's a demo-scale simulation.
CAMERA_COORDS: dict[str, tuple[float, float]] = {
    "cam01": (23.0225, 72.5714),   # Chiman Bhai Bridge — Sabarmati riverfront area
    "cam02": (23.0347, 72.5850),   # Janpath — central Ahmedabad
    "cam03": (23.0700, 72.6200),   # ONGC Office area — Chandkheda
    "cam04": (23.0082, 72.5583),   # Paldi Circle
    "cam05": (23.1100, 72.5900),   # Visat Teen Rasta
    "cam06": (21.5225, 70.4580),   # Timbavadi Gate, Junagadh
    "cam07": (20.9100, 70.3600),   # Hero showroom, Gir Somnath
    "cam08": (21.5290, 70.4640),   # Majewadi Gate, Junagadh
    "cam09": (21.5350, 70.4500),   # New bypass, Junagadh
    "cam10": (21.5200, 70.4600),   # Char Chowk Road, Junagadh
    "cam11": (21.5150, 70.4700),   # Dolatpara, Junagadh
    "cam12": (23.1640, 72.5760),   # Tri Mandir Adalaj Tollnaka — BEST for ANPR
    "cam13": (23.0300, 72.5800),   # CN Vidhyalaya, Ahmedabad
    "cam14": (23.0400, 72.5750),   # Delight RLVD, Ahmedabad
    "cam15": (23.0500, 72.5650),   # Suvidha Park, Ahmedabad
    "cam16": (23.1050, 72.5880),   # Visat P2
    "cam17": (22.3039, 70.8022),   # Rajkot Bus Port
    "cam18": (22.3100, 70.7900),   # Rajkot CCTV
    "cam19": (20.8200, 72.9300),   # Khaparia, Navsari
    "cam20": (22.5000, 72.9500),   # Mohanpura (no geocode — approximate)
    "cam21": (23.8500, 72.1300),   # Patan Dethali Char Rasta
    "cam22": (22.9800, 72.6500),   # BK Mervada Tran Rasta
    "cam23": (22.7500, 72.7000),   # Kheram
    "cam24": (23.1700, 72.9700),   # Dehgam
    "cam25": (23.2000, 72.9500),   # Dhanori
    "cam26": (23.2200, 72.9300),   # Tankal
    "cam27": (20.7600, 72.9700),   # Bilimora
    "cam28": (20.7620, 72.9720),   # Bilimora (second)
    "cam29": (20.7640, 72.9740),   # Bilimora (third)
    "cam30": (23.0700, 70.1300),   # Gandhidham Rambagh
}

SENTINEL_BASE = "https://cctv.corp8.cloud"
SENTINEL_PASSWORD = os.environ.get("SENTINEL_PASSWORD", "")

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


def get_cookie() -> str:
    resp = requests.post(
        f"{SENTINEL_BASE}/auth/login",
        data={"password": SENTINEL_PASSWORD},
        allow_redirects=False,
        timeout=10,
    )
    cookie = resp.cookies.get("sentinel") or (
        resp.headers.get("set-cookie", "").split("sentinel=")[-1].split(";")[0]
    )
    if not cookie:
        raise RuntimeError("Login failed — check password")
    return cookie


def fetch_cameras(cookie: str) -> list[dict]:
    resp = requests.get(
        f"{SENTINEL_BASE}/cameras.json",
        cookies={"sentinel": cookie},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def seed(db_url: str) -> None:
    conn = psycopg2.connect(db_url)
    conn.autocommit = False

    try:
        log.info("Fetching camera list from Sentinel sandbox…")
        cookie = get_cookie()
        cameras = fetch_cameras(cookie)
        log.info("Got %d cameras", len(cameras))

        with conn.cursor() as cur:
            inserted = skipped = 0
            for cam in cameras:
                cam_id = cam["id"]
                lat, lng = CAMERA_COORDS.get(cam_id, (None, None))
                hls_url = f"{SENTINEL_BASE}/{cam_id}/index.m3u8"

                cur.execute(
                    """
                    INSERT INTO cameras (external_id, name, lat, lng, ingest_url, status)
                    VALUES (%s, %s, %s, %s, %s, 'online')
                    ON CONFLICT (external_id) DO UPDATE
                        SET name = EXCLUDED.name,
                            lat  = EXCLUDED.lat,
                            lng  = EXCLUDED.lng,
                            ingest_url = EXCLUDED.ingest_url
                    """,
                    (cam_id, cam.get("name"), lat, lng, hls_url),
                )
                inserted += 1

            log.info("Upserted %d cameras", inserted)

            # ── Watchlist seed ────────────────────────────────────────────────
            # IMPORTANT: replace the plate value below once you watch the feeds
            # and identify a readable plate in any camera. cam12 (Adalaj Tollnaka)
            # or cam06/cam08 (gate cameras) are the most likely candidates.
            cur.execute("SELECT COUNT(*) FROM watchlist_entries")
            (existing,) = cur.fetchone()
            if existing == 0:
                cur.execute(
                    """
                    INSERT INTO watchlist_entries (type, value, reason)
                    VALUES ('plate', 'GJ01AB1234', 'Demo watchlist — replace with real plate from footage')
                    """,
                )
                log.info("Seeded 1 plate watchlist entry (placeholder — update before demo!)")
            else:
                log.info("Watchlist already has %d entries — skipping seed", existing)

        conn.commit()
        log.info("✓ Seed complete")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    from ml.config import settings
    seed(settings.database_url)


if __name__ == "__main__":
    main()
