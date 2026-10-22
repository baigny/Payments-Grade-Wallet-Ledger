from scripts.export_api import artifacts, serialized
from wallet.main import app


def test_checked_in_api_exports_match_application():
    for path, value in artifacts().items():
        assert path.read_text(encoding="utf-8") == serialized(value), (
            "Run python scripts/export_api.py after changing API endpoints"
        )


def test_swagger_describes_readiness_failure():
    responses = app.openapi()["paths"]["/health/ready"]["get"]["responses"]
    assert {"200", "503"} <= responses.keys()
    assert responses["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
