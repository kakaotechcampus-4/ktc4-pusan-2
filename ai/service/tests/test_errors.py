"""common/errors.py: 모든 실패가 {"error": {"code", "message"}} 와 정해진 상태 코드로 나간다."""

import logging

import pytest
from fastapi.testclient import TestClient


def _error(res) -> dict[str, str]:  # noqa: ANN001
    body = res.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    return body["error"]


def test_unknown_path_is_404_not_found(probe_client: TestClient) -> None:
    res = probe_client.get("/no-such-path")
    assert res.status_code == 404
    assert _error(res)["code"] == "NOT_FOUND"


def test_wrong_method_is_405_with_allow_header(probe_client: TestClient) -> None:
    res = probe_client.post("/health")
    assert res.status_code == 405
    assert _error(res)["code"] == "METHOD_NOT_ALLOWED"
    assert res.headers["allow"] == "GET"


def test_bad_body_is_422_validation_error(probe_client: TestClient) -> None:
    res = probe_client.post("/_probe/echo", json={"text": "안녕", "count": "많이"})
    assert res.status_code == 422
    assert _error(res) == {"code": "VALIDATION_ERROR", "message": "요청 형식이 올바르지 않습니다."}


def test_unknown_input_fields_do_not_break_the_shared_error_path(probe_client: TestClient) -> None:
    res = probe_client.post("/_probe/echo", json={"text": "안녕", "count": 1, "extra": True})
    assert res.status_code == 200


@pytest.mark.parametrize(
    ("kind", "status", "code"),
    [
        ("timeout", 504, "LLM_TIMEOUT"),
        ("error", 502, "LLM_ERROR"),
        ("invalid", 502, "LLM_INVALID_OUTPUT"),
        ("unavailable", 503, "LLM_UNAVAILABLE"),
    ],
)
def test_llm_errors_map_to_status_and_code(
    probe_client: TestClient, kind: str, status: int, code: str
) -> None:
    res = probe_client.get(f"/_probe/llm/{kind}")
    assert res.status_code == status
    assert _error(res)["code"] == code


def test_unhandled_error_is_500_without_internal_text(
    probe_client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    res = probe_client.get("/_probe/boom")
    assert res.status_code == 500
    assert _error(res) == {"code": "INTERNAL_ERROR", "message": "서버 오류가 발생했습니다."}
    assert "대본 원문" not in res.text
    # 원인(stack trace)은 로그에만
    assert any(r.exc_info for r in log_records)


def test_validation_log_names_fields_but_not_values(
    probe_client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    probe_client.post("/_probe/echo", json={"text": "비밀 대본 문장", "count": "많이"})
    logged = " ".join(r.getMessage() for r in log_records)
    assert "body.count" in logged
    assert "비밀 대본 문장" not in logged
    assert "많이" not in logged
