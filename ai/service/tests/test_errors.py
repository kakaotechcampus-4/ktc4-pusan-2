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
    # 로그에는 예외 종류와 위치(파일:줄:함수)만. 예외 메시지(사용자 원문일 수 있음)는 없다
    formatted = "\n".join(logging.Formatter().format(r) for r in log_records)
    assert "RuntimeError at conftest.py:" in formatted
    assert "대본 원문" not in formatted
    assert not any(r.exc_info for r in log_records)


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("timeout", "LLM 응답 시간이 초과되었습니다."),
        ("error", "LLM 호출에 실패했습니다."),
        ("invalid", "LLM 출력이 정해진 형식과 다릅니다."),
        ("unavailable", "지금은 LLM 을 쓸 수 없습니다."),
    ],
)
def test_llm_error_messages_are_fixed_korean_text(
    probe_client: TestClient, kind: str, message: str
) -> None:
    assert _error(probe_client.get(f"/_probe/llm/{kind}"))["message"] == message


def test_validation_log_hides_user_supplied_dict_keys(
    probe_client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    probe_client.post("/_probe/scores", json={"scores": {"비밀 대본 문장": "숫자 아님"}})
    logged = " ".join(r.getMessage() for r in log_records)
    assert "body.scores.<key>" in logged
    assert "비밀 대본 문장" not in logged


def test_validation_log_names_fields_but_not_values(
    probe_client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    probe_client.post("/_probe/echo", json={"text": "비밀 대본 문장", "count": "많이"})
    logged = " ".join(r.getMessage() for r in log_records)
    assert "body.count" in logged
    assert "비밀 대본 문장" not in logged
    assert "많이" not in logged


def test_dict_key_with_trailing_newline_is_hidden(
    probe_client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    probe_client.post("/_probe/scores", json={"scores": {"token\n": "숫자 아님"}})
    logged = " ".join(r.getMessage() for r in log_records)
    assert "body.scores.<key>" in logged
