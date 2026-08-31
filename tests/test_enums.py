from app.core.enums import CameraStatus, CameraType, OwnershipClass


def test_enums_are_lowercase_strings():
    assert CameraStatus.ONLINE.value == "online"
    assert CameraType.PTZ.value == "ptz"
    assert OwnershipClass.GOVERNMENT.value == "government"


def test_unknown_is_available_for_every_soft_enum():
    assert CameraStatus.UNKNOWN.value == "unknown"
    assert CameraType.OTHER.value == "other"
