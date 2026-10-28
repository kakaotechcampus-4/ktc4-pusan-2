"""common/request_log.py: X-Request-ID 를 돌려주고, 요청마다 한 줄 로그를 남긴다."""

import logging
import sys

from fastapi.testclient import TestClient

from pitch_coach_ai.common.request_log import SafeFormatter


def _request_lines(records: list[logging.LogRecord]) -> list[str]:
    """요청마다 남기는 한 줄만 고른다 (같은 로거의 unhandled_error 줄은 뺀다)."""
    messages = [r.getMessage() for r in records if r.name == "pitch_coach_ai.request"]
    return [m for m in messages if m.startswith("request_id=")]


def test_given_request_id_is_returned_as_is(client: TestClient) -> None:
    res = client.get("/health", headers={"X-Request-ID": "be-req-123"})
    assert res.headers["X-Request-ID"] == "be-req-123"


def test_missing_request_id_is_generated(client: TestClient) -> None:
    res = client.get("/health")
    request_id = res.headers["X-Request-ID"]
    assert len(request_id) == 32
    assert int(request_id, 16) >= 0  # 16진수


def test_error_responses_also_carry_request_id(probe_client: TestClient) -> None:
    for method, path in (("GET", "/no-such-path"), ("GET", "/_probe/llm/timeout")):
        res = probe_client.request(method, path, headers={"X-Request-ID": "r-1"})
        assert res.headers["X-Request-ID"] == "r-1"
    bad = probe_client.post("/_probe/echo", json={}, headers={"X-Request-ID": "r-2"})
    assert bad.headers["X-Request-ID"] == "r-2"


def test_unhandled_500_also_carries_request_id(probe_client: TestClient) -> None:
    """처리되지 않은 예외는 미들웨어 바깥에서 500 이 되면 헤더가 빠진다. 미들웨어가 잡는지 본다."""
    res = probe_client.get("/_probe/boom", headers={"X-Request-ID": "r-500"})
    assert res.status_code == 500
    assert res.headers["X-Request-ID"] == "r-500"


def test_one_log_line_per_request_with_id_path_status_time(
    client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    client.get("/health", headers={"X-Request-ID": "r-log"})
    lines = _request_lines(log_records)
    assert len(lines) == 1
    line = lines[0]
    for part in ("request_id=r-log", "method=GET", "path=/health", "status=200", "code=-"):
        assert part in line
    assert "duration_ms=" in line


def test_error_code_is_logged_next_to_status(
    probe_client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    probe_client.get("/_probe/llm/timeout")
    probe_client.get("/_probe/boom")
    lines = _request_lines(log_records)
    assert "status=504 code=LLM_TIMEOUT" in lines[0]
    assert "status=500 code=INTERNAL_ERROR" in lines[1]


def test_request_body_and_query_are_not_logged(
    probe_client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    probe_client.post("/_probe/echo?q=쿼리비밀", json={"text": "대본 원문 비밀", "count": 1})
    logged = " ".join(r.getMessage() for r in log_records)
    assert "대본 원문 비밀" not in logged
    assert "쿼리비밀" not in logged
    assert "path=/_probe/echo " in logged


def test_outside_values_cannot_forge_log_fields(
    client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    """요청 ID 의 공백 · 경로의 제어 문자를 이스케이프해 가짜 key=value 를 끼워 넣지 못한다."""
    res = client.get("/no%1B%5B2J", headers={"X-Request-ID": "fake status=200"})
    assert res.headers["X-Request-ID"] == "fake status=200"  # 응답 헤더는 받은 그대로
    line = _request_lines(log_records)[0]
    assert r"request_id=fake\x20status=200 " in line
    assert r"path=/no\x1b[2J " in line
    assert "\x1b" not in line  # 진짜 ESC 문자는 없다
    assert "status=404" in line


def test_uvicorn_access_log_is_turned_off(client: TestClient) -> None:
    """uvicorn 접근 로그는 쿼리 문자열을 싣고 요청 로그와 겹쳐서 끈다."""
    assert logging.getLogger("uvicorn.access").disabled is True


def test_exceptions_are_logged_as_kind_and_place_only() -> None:
    """`log.exception` 의 예외 메시지 · traceback 은 찍지 않는다.

    예외 메시지에 STT · 대본 원문이 섞일 수 있다(architecture.md 11절).
    """
    try:
        raise ValueError("단어 원문 비밀")
    except ValueError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "pitch_coach_ai.features.coach.core", logging.ERROR, __file__, 1, "coach decide failed",
        None, exc_info,
    )  # fmt: skip
    line = SafeFormatter("%(levelname)s %(name)s %(message)s").format(record)
    assert "coach decide failed" in line
    assert "error=ValueError at test_request_log.py:" in line
    assert "단어 원문 비밀" not in line
    assert "Traceback" not in line
