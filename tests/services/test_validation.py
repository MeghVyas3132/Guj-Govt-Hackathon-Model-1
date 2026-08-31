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


def test_iso_date_strings_are_coerced_to_date_objects():
    """CSV and JSON both deliver dates as strings; the columns are DATE. Without
    coercion the insert fails at flush time with an opaque asyncpg error."""
    from datetime import date

    result = CameraValidator().validate(
        valid_row() | {"install_date": "2023-04-11", "amc_expiry_date": "2026-12-31"}
    )
    assert result.is_valid
    assert result.values["install_date"] == date(2023, 4, 11)
    assert result.values["amc_expiry_date"] == date(2026, 12, 31)


def test_an_existing_date_object_passes_through_untouched():
    from datetime import date

    result = CameraValidator().validate(valid_row() | {"install_date": date(2023, 4, 11)})
    assert result.values["install_date"] == date(2023, 4, 11)


def test_an_unparseable_date_is_a_row_error_not_a_crash():
    result = CameraValidator().validate(valid_row() | {"install_date": "last Tuesday"})
    assert not result.is_valid
    assert any(e.code == "invalid_date" for e in result.errors)
    assert any(e.field == "install_date" for e in result.errors)


def test_an_empty_date_is_simply_absent():
    result = CameraValidator().validate(valid_row() | {"install_date": ""})
    assert result.is_valid
    assert not result.values.get("install_date")


def test_integer_columns_are_coerced_from_strings():
    result = CameraValidator().validate(valid_row() | {"retention_days": "15"})
    assert result.is_valid
    assert result.values["retention_days"] == 15
    assert isinstance(result.values["retention_days"], int)


def test_a_non_integer_retention_is_a_row_error():
    result = CameraValidator().validate(valid_row() | {"retention_days": "two weeks"})
    assert not result.is_valid
    assert any(e.code == "invalid_integer" for e in result.errors)


def test_boolean_columns_accept_the_spellings_departments_actually_use():
    for truthy in ("YES", "yes", "1", "true", "Y", "TRUE"):
        result = CameraValidator().validate(valid_row() | {"has_night_vision": truthy})
        assert result.values["has_night_vision"] is True, truthy
    for falsy in ("NO", "no", "0", "false", "N", "FALSE"):
        result = CameraValidator().validate(valid_row() | {"has_night_vision": falsy})
        assert result.values["has_night_vision"] is False, falsy


def test_an_unrecognised_boolean_is_a_row_error_not_a_silent_false():
    result = CameraValidator().validate(valid_row() | {"has_night_vision": "sometimes"})
    assert not result.is_valid
    assert any(e.code == "invalid_boolean" for e in result.errors)


def test_a_real_bool_or_int_passes_through_untouched():
    result = CameraValidator().validate(
        valid_row() | {"has_night_vision": True, "retention_days": 30}
    )
    assert result.values["has_night_vision"] is True
    assert result.values["retention_days"] == 30
