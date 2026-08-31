import pytest

from app.models.camera import Camera


@pytest.mark.asyncio
async def test_tile_containing_a_camera_returns_protobuf_bytes(
    api_client, session, seeded_department
):
    session.add(
        Camera(
            camera_uid="GJ-AMC-000001",
            department_id=seeded_department,
            external_camera_id="A-1",
            location="SRID=4326;POINT(72.5714 23.0225)",
        )
    )
    await session.commit()

    # z12 tile covering Ahmedabad
    response = await api_client.get("/api/v1/tiles/cameras/12/2873/1778.mvt")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.mapbox-vector-tile"
    assert len(response.content) > 0


@pytest.mark.asyncio
async def test_empty_tile_returns_204(api_client, seeded_department):
    response = await api_client.get("/api/v1/tiles/cameras/12/1/1.mvt")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_zoom_out_of_range_is_rejected(api_client):
    assert (await api_client.get("/api/v1/tiles/cameras/25/1/1.mvt")).status_code == 422


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7


def _protobuf_fields(buf: bytes):
    """Yield (field_number, payload) pairs from a protobuf message.

    Just enough of the wire format to count MVT features without pulling in a
    protobuf dependency: length-delimited and varint fields are decoded, the two
    fixed-width types are skipped by size.
    """
    i = 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        field, wire = key >> 3, key & 0x07
        if wire == 2:
            length, i = _read_varint(buf, i)
            yield field, buf[i : i + length]
            i += length
        elif wire == 0:
            value, i = _read_varint(buf, i)
            yield field, value
        elif wire in (1, 5):
            width = 8 if wire == 1 else 4
            yield field, buf[i : i + width]
            i += width
        else:
            raise AssertionError(f"unsupported protobuf wire type {wire}")


def mvt_feature_counts(tile: bytes) -> dict[str, int]:
    """Map layer name -> feature count. Tile.layers is field 3; Layer.name is
    field 1 and Layer.features is repeated field 2."""
    counts: dict[str, int] = {}
    for field, payload in _protobuf_fields(tile):
        if field != 3:
            continue
        name, features = "", 0
        for layer_field, layer_payload in _protobuf_fields(payload):
            if layer_field == 1:
                name = layer_payload.decode()
            elif layer_field == 2:
                features += 1
        counts[name] = features
    return counts


@pytest.mark.asyncio
async def test_low_zoom_collapses_nearby_cameras_into_fewer_features(
    api_client, session, seeded_department
):
    # Twelve cameras inside ~150 m of each other. At z8 the grid cell is ~24 km,
    # so every one of them must land in the same cluster.
    for i in range(12):
        session.add(
            Camera(
                camera_uid=f"GJ-AMC-{i:06d}",
                department_id=seeded_department,
                external_camera_id=f"A-{i}",
                location=f"SRID=4326;POINT({72.5714 + i * 0.0001} {23.0225 + i * 0.0001})",
            )
        )
    await session.commit()

    points = await api_client.get("/api/v1/tiles/cameras/12/2873/1778.mvt")
    assert points.status_code == 200
    assert mvt_feature_counts(points.content) == {"cameras": 12}

    clusters = await api_client.get("/api/v1/tiles/cameras/8/179/111.mvt")
    assert clusters.status_code == 200
    counts = mvt_feature_counts(clusters.content)
    assert set(counts) == {"camera_clusters"}
    assert 0 < counts["camera_clusters"] < 12
