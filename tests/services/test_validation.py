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


# ---- a missing field must be explainable from the operator's own file --------

def test_a_missing_field_names_the_column_the_department_reads_it_from():
    """"external_camera_id is required" is true but useless to someone looking
    at a spreadsheet with no such column."""
    result = CameraValidator().validate(
        {},
        column_map={"AssetCode": "external_camera_id"},
        source_columns=["cam_id", "name", "lat", "lng"],
    )
    message = next(e.message for e in result.errors if e.field == "external_camera_id")
    assert "'AssetCode'" in message
    assert "not in the file" in message
    assert "cam_id" in message  # what they actually sent


def test_a_field_the_mapping_never_mentions_says_so():
    """Different fix: the department's mapping is incomplete, not the file."""
    result = CameraValidator().validate(
        {"external_camera_id": "x", "latitude": 23.0},
        column_map={"AssetCode": "external_camera_id"},
        source_columns=["AssetCode"],
    )
    message = next(e.message for e in result.errors if e.field == "longitude")
    assert "does not say which column supplies" in message


def test_no_hint_is_added_when_the_column_was_present():
    """The column exists and is simply empty; naming it would mislead."""
    result = CameraValidator().validate(
        {},
        column_map={"AssetCode": "external_camera_id"},
        source_columns=["AssetCode"],
    )
    message = next(e.message for e in result.errors if e.field == "external_camera_id")
    assert message == "external_camera_id is required."


def test_no_hint_without_a_mapping():
    """Manual entry and the API have no column map; the bare message is right."""
    result = CameraValidator().validate({})
    assert all(e.message.endswith("is required.") for e in result.errors)


def test_several_fields_missing_produce_distinct_errors():
    """They share a code, so anything keying on code alone loses some."""
    result = CameraValidator().validate({})
    codes = [e.code for e in result.errors]
    fields = [e.field for e in result.errors]
    assert codes.count("missing_required_field") == 3
    assert sorted(fields) == ["external_camera_id", "latitude", "longitude"]
