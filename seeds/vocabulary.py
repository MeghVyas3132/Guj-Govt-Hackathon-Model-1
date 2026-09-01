"""Seed the shipped vocabulary terms.

These are defaults, not limits. A department with a camera type nobody here
anticipated adds a row; no deploy, no migration -- the cameras columns are VARCHAR.

camera_type terms carry their own coverage geometry so the gap analysis reads
ranges from data rather than hardcoding "PTZ means 250 m" in SQL.
"""

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.vocabulary import VocabularyTerm

# (dimension, code, label, is_fallback, sort, range_m, fov_deg, omnidirectional)
TERMS: list[tuple] = [
    # Camera types, with the coverage geometry the gap analysis uses.
    ("camera_type", "fixed", "Fixed", False, 10, 100.0, 90.0, False),
    ("camera_type", "ptz", "PTZ", False, 20, 250.0, None, True),
    ("camera_type", "dome", "Dome", False, 30, 250.0, None, True),
    ("camera_type", "bullet", "Bullet", False, 40, 100.0, 75.0, False),
    ("camera_type", "anpr", "ANPR", False, 50, 60.0, 60.0, False),
    ("camera_type", "thermal", "Thermal", False, 60, 150.0, 45.0, False),
    ("camera_type", "other", "Other / unclassified", True, 900, 100.0, None, True),

    ("status", "online", "Online", False, 10, None, None, None),
    ("status", "offline", "Offline", False, 20, None, None, None),
    ("status", "maintenance", "Maintenance", False, 30, None, None, None),
    ("status", "unknown", "Unknown", True, 900, None, None, None),

    ("connectivity", "fiber", "Fibre", False, 10, None, None, None),
    ("connectivity", "4g", "4G", False, 20, None, None, None),
    ("connectivity", "5g", "5G", False, 30, None, None, None),
    ("connectivity", "wifi", "Wi-Fi", False, 40, None, None, None),
    ("connectivity", "lan", "LAN", False, 50, None, None, None),
    ("connectivity", "unknown", "Unknown", True, 900, None, None, None),

    ("camera_technology", "ip", "IP", False, 10, None, None, None),
    ("camera_technology", "analog", "Analog", False, 20, None, None, None),
    ("camera_technology", "unknown", "Unknown", True, 900, None, None, None),

    ("ownership_class", "government", "Government", True, 10, None, None, None),
    ("ownership_class", "private", "Private", False, 20, None, None, None),
    ("ownership_class", "ppp", "Public-private", False, 30, None, None, None),

    ("site_type", "traffic_junction", "Traffic junction", False, 10, None, None, None),
    ("site_type", "public_space", "Public space", False, 20, None, None, None),
    ("site_type", "bus_depot", "Bus depot", False, 30, None, None, None),
    ("site_type", "hospital", "Hospital", False, 40, None, None, None),
    ("site_type", "office", "Office", False, 50, None, None, None),
    ("site_type", "godown", "Godown", False, 60, None, None, None),
    ("site_type", "pds_shop", "PDS shop", False, 70, None, None, None),
    ("site_type", "rto_checkpoint", "RTO checkpoint", False, 80, None, None, None),
    ("site_type", "border_checkpost", "Border check-post", False, 90, None, None, None),
    ("site_type", "other", "Other", True, 900, None, None, None),

    ("storage_type", "local", "Local", False, 10, None, None, None),
    ("storage_type", "cloud", "Cloud", False, 20, None, None, None),
    ("storage_type", "unknown", "Unknown", True, 900, None, None, None),

    ("stream_protocol", "hls", "HLS", False, 10, None, None, None),
    ("stream_protocol", "rtsp", "RTSP", False, 20, None, None, None),
    ("stream_protocol", "whep", "WebRTC (WHEP)", False, 30, None, None, None),
    ("stream_protocol", "onvif", "ONVIF", False, 40, None, None, None),
    ("stream_protocol", "snapshot", "Snapshot", False, 50, None, None, None),

    ("reachability", "public_cdn", "Public CDN", False, 10, None, None, None),
    ("reachability", "direct_ip", "Direct IP", False, 20, None, None, None),
    ("reachability", "lan_only", "LAN only", False, 30, None, None, None),
]


async def main() -> None:
    async with SessionLocal() as session:
        existing = {
            (row.dimension, row.code): row
            for row in (await session.execute(select(VocabularyTerm))).scalars().all()
        }
        added = 0
        for dim, code, label, fallback, order, rng, fov, omni in TERMS:
            row = existing.get((dim, code))
            if row is None:
                session.add(
                    VocabularyTerm(
                        dimension=dim, code=code, label=label, is_fallback=fallback,
                        sort_order=order, coverage_range_m=rng, coverage_fov_deg=fov,
                        is_omnidirectional=omni,
                    )
                )
                added += 1
            else:
                # Refresh the shipped attributes without clobbering operator edits
                # to is_active, which is how a term gets retired.
                row.label, row.is_fallback, row.sort_order = label, fallback, order
                row.coverage_range_m, row.coverage_fov_deg = rng, fov
                row.is_omnidirectional = omni
        await session.commit()
    print(f"Vocabulary: {len(TERMS)} terms ({added} new)")


if __name__ == "__main__":
    asyncio.run(main())
