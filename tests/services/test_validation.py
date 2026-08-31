from app.services.validation import CameraValidator


def valid_row() -> dict:
    return {"external_camera_id": "A-1", "latitude": 23.0225, "longitude": 72.5714}


def test_accepts_a_well_formed_row():
    result = CameraValidator().validate(valid_row())
    assert result.is_valid
    assert result.errors == []


def test_rejects_missing_external_camera_id():
    row = valid_row()
    del row["external_camera_id"]
    result = CameraValidator().validate(row)
    assert not result.is_valid
    assert any(e.field == "external_camera_id" for e in result.errors)


def test_rejects_unparseable_coordinates():
    row = valid_row() | {"latitude": "not-a-number"}
    result = CameraValidator().validate(row)
    assert not result.is_valid
    assert any(e.code == "invalid_coordinate" for e in result.errors)


def test_rejects_coordinates_outside_gujarat():
    row = valid_row() | {"latitude": 28.6139, "longitude": 77.2090}  # New Delhi
    result = CameraValidator().validate(row)
    assert not result.is_valid
    assert any(e.code == "outside_gujarat" for e in result.errors)


def test_rejects_out_of_range_azimuth():
    result = CameraValidator().validate(valid_row() | {"azimuth_deg": 400})
    assert not result.is_valid
    assert any(e.field == "azimuth_deg" for e in result.errors)


def test_accepts_boundary_azimuth_values():
    assert CameraValidator().validate(valid_row() | {"azimuth_deg": 0}).is_valid
    assert CameraValidator().validate(valid_row() | {"azimuth_deg": 359.9}).is_valid
