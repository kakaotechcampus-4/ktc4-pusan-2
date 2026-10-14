"""common/request_log.py: X-Request-ID 를 돌려주고, 요청마다 한 줄 로그를 남긴다."""

import logging

from fastapi.testclient import TestClient


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
