"""POST /sample/judge: examples 그대로 응답 · meta, 끄면 404, 422 공통 본문, X-Request-ID, 로그."""

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pitch_coach_ai.features.sample import core
from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import Settings

EXAMPLES = Path(core.__file__).parent / "examples"


def _example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def test_example_request_gives_example_response(sample_client: TestClient) -> None:
    res = sample_client.post("/sample/judge", json=_example("judge_request.json"))
    assert res.status_code == 200
    assert res.json() == _example("judge_response.json")


def test_sample_is_off_by_default(client: TestClient) -> None:
    res = client.post("/sample/judge", json=_example("judge_request.json"))
    assert res.status_code == 404
    assert res.json() == {
        "error": {"code": "NOT_FOUND", "message": "요청한 경로를 찾을 수 없습니다."}
    }


def test_env_value_turns_sample_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_ENABLE_SAMPLE", "true")
    with TestClient(create_app(Settings(_env_file=None))) as c:
        assert c.post("/sample/judge", json=_example("judge_request.json")).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"t_ms": "지금", "since_ms": 0, "records": []},
        {"t_ms": 1000, "since_ms": 0},
        {"t_ms": 1000, "since_ms": -1, "records": []},
        {"t_ms": 1000, "since_ms": 0, "records": [{"duration_ms": 1000}]},
    ],
    ids=["t_ms-not-int", "records-missing", "since_ms-negative", "record-without-t_ms"],
)
def test_bad_request_is_422_common_body(sample_client: TestClient, body: dict) -> None:
    res = sample_client.post("/sample/judge", json=body)
    assert res.status_code == 422
    assert res.json() == {
        "error": {"code": "VALIDATION_ERROR", "message": "요청 형식이 올바르지 않습니다."}
    }


def test_request_id_is_returned_as_is(sample_client: TestClient) -> None:
    res = sample_client.post(
        "/sample/judge",
        json=_example("judge_request.json"),
        headers={"X-Request-ID": "sample-check-1"},
    )
    assert res.headers["x-request-id"] == "sample-check-1"


def test_one_log_line_without_request_values(
    sample_client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    sample_client.post("/sample/judge", json=_example("judge_request.json"))
    lines = [r.getMessage() for r in log_records if r.name == "pitch_coach_ai.request"]
    assert len(lines) == 1
    assert "path=/sample/judge status=200 code=-" in lines[0]
    assert "CAMERA" not in lines[0]
