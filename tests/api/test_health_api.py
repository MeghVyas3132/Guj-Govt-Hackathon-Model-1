from datetime import UTC, datetime, timedelta

import pytest

from app.models.camera import Camera


@pytest.fixture
async def cameras(session, seeded_department):
    rows = []
    for index in range(1, 4):
        cam = Camera(
            camera_uid=f"GJ-AMC-00000{index}",
            department_id=seeded_department,
            external_camera_id=f"A-{index}",
            location=f"SRID=4326;POINT(72.5{index} 23.0{index})",
            current_status="unknown",
        )
        session.add(cam)
        rows.append(cam)
    await session.commit()
    return rows


@pytest.mark.asyncio
async def test_batch_push_by_external_id(api_client, cameras, seeded_department):
    response = await api_client.post(
        f"/api/v1/health/observations?department_id={seeded_department}",
        json=[
            {"external_camera_id": "A-1", "status": "online"},
            {"external_camera_id": "A-2", "status": "offline"},
        ],
    )
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 2
    assert body["unmatched"] == []


@pytest.mark.asyncio
async def test_unknown_external_id_is_reported_not_fatal(
    api_client, cameras, seeded_department
):
    response = await api_client.post(
        f"/api/v1/health/observations?department_id={seeded_department}",
        json=[
            {"external_camera_id": "A-1", "status": "online"},
            {"external_camera_id": "GHOST", "status": "offline"},
        ],
    )
    assert response.status_code == 202
    assert response.json()["accepted"] == 1
    assert response.json()["unmatched"] == ["GHOST"]


@pytest.mark.asyncio
async def test_offline_list_is_sorted_by_longest_downtime_first(
    api_client, session, cameras, seeded_department
):
    now = datetime.now(UTC)
    cameras[0].current_status = "offline"
    cameras[0].status_since = now - timedelta(days=9)
    cameras[1].current_status = "offline"
    cameras[1].status_since = now - timedelta(hours=3)
    await session.commit()

    response = await api_client.get("/api/v1/health/offline")
    items = response.json()["items"]

    assert [i["camera_uid"] for i in items] == ["GJ-AMC-000001", "GJ-AMC-000002"]
    assert items[0]["downtime_seconds"] > items[1]["downtime_seconds"]
    assert items[0]["downtime_seconds"] > 7 * 86400


@pytest.mark.asyncio
async def test_summary_counts_by_status_and_downtime_band(api_client, session, cameras):
    now = datetime.now(UTC)
    cameras[0].current_status = "offline"
    cameras[0].status_since = now - timedelta(days=9)
    cameras[1].current_status = "online"
    cameras[2].current_status = "maintenance"
    await session.commit()

    summary = (await api_client.get("/api/v1/health/summary")).json()
    assert summary["total"] == 3
    assert summary["offline"] == 1
    assert summary["online"] == 1
    assert summary["maintenance"] == 1
    assert summary["offline_over_7d"] == 1
