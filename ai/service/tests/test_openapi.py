"""커밋된 API 계약(`contracts/openapi.json`)이 지금 코드에서 만든 것과 같은지 본다.

다르면 `uv run python tools/export_openapi.py` 로 다시 만들어 같은 PR 에 넣는다.
"""

import importlib.util
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]


def _export_module():
    spec = importlib.util.spec_from_file_location(
        "export_openapi", SERVICE / "tools" / "export_openapi.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_openapi_matches_the_code() -> None:
    export = _export_module()
    committed = export.OUT.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert committed == export.render(), "uv run python tools/export_openapi.py 로 다시 만든다"


def test_feature_paths_are_listed_without_api_prefix() -> None:
    import json

    paths = set(json.loads(_export_module().render())["paths"])
    assert paths == {"/health", "/ready", "/scripts/rubric", "/takes/analyze", "/takes/rescore"}


def test_error_responses_document_the_common_error_body() -> None:
    import json

    doc = json.loads(_export_module().render())
    for path in ("/scripts/rubric", "/takes/analyze", "/takes/rescore"):
        responses = doc["paths"][path]["post"]["responses"]
        assert set(responses) == {"200", "422", "500", "502", "503", "504"}
        schema = responses["422"]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ErrorResponse"}
    assert "503" in doc["paths"]["/ready"]["get"]["responses"]
    assert "HTTPValidationError" not in doc["components"]["schemas"]
