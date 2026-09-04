"""
ml.auth
=======
Sentinel camera sandbox authentication helpers.

The Sentinel sandbox at ``cctv.corp8.cloud`` uses session-cookie auth for its
HLS streams.  The login endpoint issues the cookie via a 302 redirect — the
cookie lives in the ``Set-Cookie`` header of that redirect response, so we
must *not* follow the redirect automatically.

RTSP streams (``rtsp://103.250.160.189:8554/stream/<id>``) require **no**
authentication and can be opened directly by OpenCV/FFmpeg.

Typical usage::

    from ml.auth import get_cookie, get_ffmpeg_options

    cookie = get_cookie()                       # cached module-level call
    opts   = get_ffmpeg_options(cookie)         # pass to cv2.VideoCapture env
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import requests

from ml.config import settings

logger = logging.getLogger(__name__)

# ── Module-level cookie cache ─────────────────────────────────────────────────
# Protected by a lock so the worker can safely refresh from any thread.
_cookie_lock: threading.Lock = threading.Lock()
_cached_cookie: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def get_sentinel_cookie(password: str) -> str:
    """Authenticate with the Sentinel sandbox and return the session cookie.

    Performs a ``POST /auth/login`` with ``application/x-www-form-urlencoded``
    form data containing the password.  The server responds with a **302
    Found** that sets the ``sentinel`` cookie; we intentionally disable
    redirect-following so we can extract the cookie from that response's
    ``Set-Cookie`` header before the client would discard it.

    Args:
        password: Plaintext password for the Sentinel sandbox.

    Returns:
        The raw value of the ``sentinel`` session cookie (the part after
        ``sentinel=``, before the first ``;``).

    Raises:
        RuntimeError: If the login endpoint does not return a 302, or if the
            expected ``sentinel`` cookie is absent from the response headers.
        requests.RequestException: On any network-level failure.
    """
    login_url = f"{settings.sentinel_base_url}/auth/login"
    logger.debug("Authenticating with Sentinel sandbox at %s", login_url)

    # allow_redirects=False is critical: the cookie is in the *302* response,
    # not in any subsequent page.
    response = requests.post(
        login_url,
        data={"email": settings.sentinel_email, "password": password},
        allow_redirects=False,
        timeout=10,
    )

    if response.status_code != 302:
        raise RuntimeError(
            f"Expected 302 from Sentinel login, got {response.status_code}. "
            f"Body: {response.text[:200]!r}"
        )

    # requests surfaces Set-Cookie cookies via response.cookies
    cookie_value = response.cookies.get("sentinel")

    # Fallback: parse Set-Cookie header manually in case the jar missed it.
    if cookie_value is None:
        raw_header = response.headers.get("Set-Cookie", "")
        for part in raw_header.split(";"):
            part = part.strip()
            if part.startswith("sentinel="):
                cookie_value = part[len("sentinel="):]
                break

    if not cookie_value:
        raise RuntimeError(
            "Sentinel login returned 302 but 'sentinel' cookie was not found "
            f"in Set-Cookie header. Headers: {dict(response.headers)}"
        )

    logger.info("Sentinel cookie obtained successfully (length=%d)", len(cookie_value))
    return cookie_value


def get_ffmpeg_options(cookie: str) -> str:
    """Build the ``OPENCV_FFMPEG_CAPTURE_OPTIONS`` value for HLS cookie auth.

    OpenCV reads the env-var ``OPENCV_FFMPEG_CAPTURE_OPTIONS`` (or the value
    passed via ``cv2.VideoCapture(..., apiPreference, params)`` in newer
    OpenCV) to inject custom FFmpeg options.  The format is a
    semicolon-separated list of ``key;value`` pairs.

    For HLS streams protected by the Sentinel cookie we inject a custom HTTP
    ``Cookie`` header.  The ``\\r\\n`` suffix is required by FFmpeg's
    ``headers`` option to terminate the injected header line.

    Args:
        cookie: The raw Sentinel cookie *value* (without the ``sentinel=``
            prefix).

    Returns:
        A string in the format ``'headers;Cookie: sentinel=<value>\\r\\n'``
        suitable for assignment to ``OPENCV_FFMPEG_CAPTURE_OPTIONS``.

    Example::

        import os
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = get_ffmpeg_options(cookie)
        cap = cv2.VideoCapture(hls_url)
    """
    return f"headers;Cookie: sentinel={cookie}\r\n"


def get_cookie(*, refresh: bool = False) -> str:
    """Return the module-level cached Sentinel cookie, fetching if necessary.

    Thread-safe.  On the first call (or when *refresh* is ``True``) it calls
    :func:`get_sentinel_cookie` using the password from :data:`ml.config.settings`.
    Subsequent calls return the in-memory cached value without any network
    round-trip.

    This function is the preferred way to obtain the cookie in the worker loop:
    call it once at startup, then call with ``refresh=True`` after any
    stream-auth failure.

    Args:
        refresh: If ``True``, bypass the cache and re-authenticate even if a
            cached cookie already exists.

    Returns:
        The raw ``sentinel`` cookie value.

    Raises:
        RuntimeError: Propagated from :func:`get_sentinel_cookie` on auth
            failure.
    """
    global _cached_cookie  # noqa: PLW0603

    with _cookie_lock:
        if _cached_cookie is None or refresh:
            action = "Refreshing" if (_cached_cookie is not None) else "Fetching"
            logger.info("%s Sentinel session cookie…", action)
            _cached_cookie = get_sentinel_cookie(settings.sentinel_password)

        return _cached_cookie
