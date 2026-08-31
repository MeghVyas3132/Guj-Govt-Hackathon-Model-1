from app.core.enums import CameraStatus, CameraType
from app.services.normalization import FieldMappingResolver

AMC_CONFIG = {
    "column_map": {
        "cam_id": "external_camera_id",
        "lat": "latitude",
        "lng": "longitude",
        "cam_kind": "camera_type",
        "state": "status",
    },
    "value_maps": {
        "status": {"ACTIVE": "online", "DOWN": "offline", "AMC": "maintenance"},
        "camera_type": {"PTZ-DOME": "ptz", "BULLET": "fixed"},
    },
    "defaults": {"connectivity": "fiber"},
    "passthrough_to_metadata": True,
}


def test_renames_columns_to_canonical_names():
    result = FieldMappingResolver(AMC_CONFIG).resolve(
        {"cam_id": "A-1", "lat": "23.02", "lng": "72.57"}
    )
    assert result.values["external_camera_id"] == "A-1"
    assert result.values["latitude"] == "23.02"


def test_translates_department_vocabulary_to_canonical_enums():
    result = FieldMappingResolver(AMC_CONFIG).resolve(
        {"cam_id": "A-1", "state": "ACTIVE", "cam_kind": "PTZ-DOME"}
    )
    assert result.values["status"] == CameraStatus.ONLINE
    assert result.values["camera_type"] == CameraType.PTZ


def test_unmapped_value_falls_back_and_warns_but_does_not_fail():
    result = FieldMappingResolver(AMC_CONFIG).resolve({"cam_id": "A-1", "state": "FLAKY"})
    assert result.values["status"] == CameraStatus.UNKNOWN
    assert any("FLAKY" in w for w in result.warnings)


def test_unmapped_columns_are_preserved_in_metadata_not_dropped():
    result = FieldMappingResolver(AMC_CONFIG).resolve(
        {"cam_id": "A-1", "pole_number": "P-77", "ward_engineer": "R. Patel"}
    )
    assert result.metadata == {"pole_number": "P-77", "ward_engineer": "R. Patel"}


def test_defaults_apply_only_when_field_absent():
    resolver = FieldMappingResolver(AMC_CONFIG)
    assert resolver.resolve({"cam_id": "A-1"}).values["connectivity"] == "fiber"
    supplied = resolver.resolve({"cam_id": "A-1", "connectivity": "4g"})
    assert supplied.values["connectivity"] == "4g"


def test_passthrough_disabled_drops_unmapped_columns():
    config = {**AMC_CONFIG, "passthrough_to_metadata": False}
    result = FieldMappingResolver(config).resolve({"cam_id": "A-1", "pole_number": "P-77"})
    assert result.metadata == {}


def test_dms_coordinates_are_converted_to_decimal_degrees():
    config = {**AMC_CONFIG, "coordinate_format": "dms"}
    result = FieldMappingResolver(config).resolve(
        {"cam_id": "A-1", "lat": "23 01 21.0 N", "lng": "72 34 17.0 E"}
    )
    assert round(result.values["latitude"], 4) == 23.0225
    assert round(result.values["longitude"], 4) == 72.5714


def test_dms_southern_and_western_hemispheres_are_negative():
    config = {**AMC_CONFIG, "coordinate_format": "dms"}
    result = FieldMappingResolver(config).resolve(
        {"cam_id": "A-1", "lat": "23 01 21.0 S", "lng": "72 34 17.0 W"}
    )
    assert round(result.values["latitude"], 4) == -23.0225
    assert round(result.values["longitude"], 4) == -72.5714


def test_private_underscore_keys_are_skipped_entirely():
    """`_stream_endpoints` is a private channel through the payload (Plan 2, Task 1).

    It must never be mapped into values nor leak into cameras.metadata.
    """
    result = FieldMappingResolver(AMC_CONFIG).resolve(
        {"cam_id": "A-1", "_stream_endpoints": [{"protocol": "rtsp", "url": "rtsp://x/y"}]}
    )
    assert "_stream_endpoints" not in result.metadata
    assert "_stream_endpoints" not in result.values
    assert result.metadata == {}


def test_dms_accepts_symbol_delimited_form_from_excel_and_gis_exports():
    config = {**AMC_CONFIG, "coordinate_format": "dms"}
    result = FieldMappingResolver(config).resolve(
        {"cam_id": "A-1", "lat": "23°01'21.0\" N", "lng": "72°34'17.0\" E"}
    )
    assert round(result.values["latitude"], 4) == 23.0225
    assert round(result.values["longitude"], 4) == 72.5714


def test_symbol_delimited_dms_keeps_the_hemisphere_sign():
    config = {**AMC_CONFIG, "coordinate_format": "dms"}
    result = FieldMappingResolver(config).resolve(
        {"cam_id": "A-1", "lat": "23°01'21.0\" S", "lng": "72°34'17.0\" W"}
    )
    assert round(result.values["latitude"], 4) == -23.0225
    assert round(result.values["longitude"], 4) == -72.5714


def test_unparseable_dms_warns_and_leaves_the_raw_value_for_the_validator():
    """A bad coordinate must become a row error, never an exception that kills the batch."""
    config = {**AMC_CONFIG, "coordinate_format": "dms"}
    result = FieldMappingResolver(config).resolve(
        {"cam_id": "A-1", "lat": "somewhere near the bridge", "lng": "72 34 17.0 E"}
    )
    assert result.values["latitude"] == "somewhere near the bridge"
    assert any("latitude" in w for w in result.warnings)
    assert round(result.values["longitude"], 4) == 72.5714
