from scripts.export_openapi import TARGET, render


def test_committed_openapi_spec_is_current():
    """The exported spec is the artifact other teams code against, so it drifting
    silently is worse than it being absent. Regenerate with:

        python -m scripts.export_openapi
    """
    assert TARGET.exists(), f"{TARGET} is missing; run python -m scripts.export_openapi"
    assert TARGET.read_text() == render(), (
        f"{TARGET} is stale. Run: python -m scripts.export_openapi"
    )


def test_spec_documents_every_onboarding_path():
    import json

    paths = json.loads(TARGET.read_text())["paths"]
    for route in (
        "/api/v1/onboarding/preview",
        "/api/v1/onboarding/import",
        "/api/v1/onboarding/bulk",
        "/api/v1/cameras/{camera_id}/streams",
        "/api/v1/connectors",
        "/api/v1/connectors/{code}/sync",
        "/api/v1/tiles/cameras/{z}/{x}/{y}.mvt",
    ):
        assert route in paths, f"{route} missing from the exported spec"
