"""Quick connectivity check for all Sentinel camera feeds.

Run before starting the worker to verify streams are reachable
and identify which cameras have the best feeds for the demo.

Usage:
    python -m ml.scripts.check_feeds

Output: per-camera status (RTSP reachable, HLS reachable, frame grab OK)
"""

import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import cv2
import requests

SENTINEL_BASE = "https://cctv.corp8.cloud"
SENTINEL_PASSWORD = os.environ.get("SENTINEL_PASSWORD", "")
RTSP_BASE = "rtsp://103.250.160.189:8554/stream"
RTSP_HOST = "103.250.160.189"
RTSP_PORT = 8554


def get_cookie() -> str:
    resp = requests.post(
        f"{SENTINEL_BASE}/auth/login",
        data={"password": SENTINEL_PASSWORD},
        allow_redirects=False,
        timeout=10,
    )
    return resp.cookies.get("sentinel", "")


def check_rtsp_port() -> bool:
    """Check if the RTSP gateway port is reachable (fast TCP probe)."""
    try:
        s = socket.create_connection((RTSP_HOST, RTSP_PORT), timeout=3)
        s.close()
        return True
    except OSError:
        return False


def check_hls(cam_id: str, cookie: str) -> bool:
    """Check if HLS manifest is accessible."""
    try:
        resp = requests.head(
            f"{SENTINEL_BASE}/{cam_id}/index.m3u8",
            cookies={"sentinel": cookie},
            timeout=5,
        )
        return resp.status_code == 200
    except Exception:
        return False


def grab_frame(cam_id: str, cookie: str, use_rtsp: bool) -> tuple[bool, float | None]:
    """
    Attempt to grab one frame from a camera.

    Returns (success, frame_grab_time_seconds).
    """
    if use_rtsp:
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        url = f"{RTSP_BASE}/{cam_id}"
    else:
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            f"headers;Cookie: sentinel={cookie}\\r\\n"
        )
        url = f"{SENTINEL_BASE}/{cam_id}/index.m3u8"

    t0 = time.time()
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    ok, frame = cap.read()
    elapsed = time.time() - t0
    cap.release()

    if ok and frame is not None and frame.size > 0:
        return True, elapsed
    return False, None


def check_camera(cam_id: str, cam_name: str, cookie: str, rtsp_ok: bool) -> dict:
    hls_ok = check_hls(cam_id, cookie)
    frame_ok, frame_time = False, None

    if rtsp_ok:
        frame_ok, frame_time = grab_frame(cam_id, cookie, use_rtsp=True)
        method = "rtsp"
    if not frame_ok and hls_ok:
        frame_ok, frame_time = grab_frame(cam_id, cookie, use_rtsp=False)
        method = "hls"
    elif not frame_ok:
        method = "none"

    return {
        "id": cam_id,
        "name": cam_name,
        "hls": hls_ok,
        "rtsp": rtsp_ok,
        "frame": frame_ok,
        "method": method,
        "time": frame_time,
    }


def main() -> None:
    print("Setu feed checker — testing all Sentinel cameras\n")

    print("Authenticating…", end=" ", flush=True)
    cookie = get_cookie()
    if not cookie:
        print("FAILED — check password")
        sys.exit(1)
    print("OK ✓")

    print("Fetching camera list…", end=" ", flush=True)
    cameras = requests.get(
        f"{SENTINEL_BASE}/cameras.json",
        cookies={"sentinel": cookie},
        timeout=10,
    ).json()
    print(f"{len(cameras)} cameras found ✓")

    print(f"Testing RTSP port {RTSP_HOST}:{RTSP_PORT}…", end=" ", flush=True)
    rtsp_reachable = check_rtsp_port()
    print("OPEN ✓" if rtsp_reachable else "BLOCKED ✗ (will use HLS fallback)")

    print("\nChecking each camera (parallel, this takes ~30s)…\n")

    results = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(check_camera, c["id"], c["name"], cookie, rtsp_reachable): c
            for c in cameras
        }
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            status = "✓" if r["frame"] else "✗"
            time_str = f"{r['time']:.1f}s" if r["time"] else "—"
            print(
                f"  {status} {r['id']:6s}  hls={'Y' if r['hls'] else 'N'}  "
                f"frame={'Y' if r['frame'] else 'N'}  via={r['method']:4s}  "
                f"t={time_str:5s}  {r['name']}"
            )

    online = [r for r in results if r["frame"]]
    print(f"\n{'─'*60}")
    print(f"  {len(online)}/{len(results)} cameras have readable frames")
    print(f"  Primary protocol: {'RTSP' if rtsp_reachable else 'HLS'}")

    if online:
        print(f"\n  Recommended cameras for demo (first 6 online):")
        for r in sorted(online, key=lambda x: x["id"])[:6]:
            print(f"    {r['id']} — {r['name']}")
        ids = ",".join(r["id"] for r in sorted(online, key=lambda x: x["id"])[:6])
        print(f"\n  Set in .env:  CAMERAS={ids}")


if __name__ == "__main__":
    main()
