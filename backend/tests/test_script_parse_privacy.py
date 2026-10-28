"""AI 오류와 재시도 로그에 사용자 원문을 남기지 않는다."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from pitch_coach_backend.module.pitch.adapter.script_parser import (
    HttpScriptParser,
    ParsedScript,
    ParsedSlide,
    ScriptParseError,
)
from pitch_coach_backend.module.pitch.dto import ParseTicket, ScriptParseErrorCode
from pitch_coach_backend.module.pitch.service import script_parse_runner
from pitch_coach_backend.module.pitch.service.script_parse_runner import ScriptParseRunner

PRIVATE = "PRIVATE_SCRIPT_AUDIT_MARKER"


@pytest.mark.parametrize("status", [422, 503])
@pytest.mark.parametrize(
    "body",
    [
        {"error": {"code": "LLM_ERROR", "message": PRIVATE}},
        {"error": {"code": PRIVATE, "message": PRIVATE}},
        {"error": {"code": {"input": PRIVATE}, "message": PRIVATE}},
        [PRIVATE],
        PRIVATE,
    ],
)
def test_error_and_runner_logs_omit_response_body(status, body, caplog):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json=body)

    parser = HttpScriptParser(
        "http://ai.test", transport=httpx.MockTransport(respond), retry_delay_sec=0
    )
    runner = ScriptParseRunner(parser)
    runner._save_failure = AsyncMock()
    ticket = ParseTicket(uuid.uuid4(), datetime.now(UTC), PRIVATE, 300000)
    with caplog.at_level(logging.WARNING):
        asyncio.run(runner.run(ticket))
    assert PRIVATE not in caplog.text
    assert "code=AI_" in caplog.text
    assert f"status={status}" in caplog.text
    assert requests[0].headers["X-Request-ID"] in caplog.text
    assert len(requests) == (2 if status == 503 else 1)
    assert len({request.headers["X-Request-ID"] for request in requests}) == 1
    runner._save_failure.assert_awaited_once()


def test_plain_text_error_is_not_logged(caplog):
    parser = HttpScriptParser(
        "http://ai.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(503, text=PRIVATE)),
        retry_delay_sec=0,
    )
    with caplog.at_level(logging.WARNING), pytest.raises(ScriptParseError) as error:
        asyncio.run(parser.parse(PRIVATE, 300000))
    assert PRIVATE not in caplog.text
    assert PRIVATE not in str(error.value)
    assert "ai_code=UNKNOWN" in caplog.text


def test_separate_parse_requests_use_separate_ids():
    ids = []

    def respond(request):
        ids.append(request.headers["X-Request-ID"])
        return httpx.Response(422)

    parser = HttpScriptParser("http://ai.test", transport=httpx.MockTransport(respond))
    for _ in range(2):
        with pytest.raises(ScriptParseError):
            asyncio.run(parser.parse("대본", 1000))
    assert len(set(ids)) == 2


class _DriverError(Exception):
    """psycopg 예외처럼 SQLSTATE 를 들고 있다. 본문에는 DB 가 돌려준 값이 섞일 수 있다."""

    sqlstate = "08006"


def _db_error() -> OperationalError:
    # SQLAlchemy 예외 문자열에는 SQL 매개변수와 드라이버 메시지가 둘 다 붙는다
    return OperationalError(
        "UPDATE script_slides SET full_content=%(full_content)s",
        {"full_content": PRIVATE},
        _DriverError(f"connection lost while writing {PRIVATE}"),
    )


class _Parser:
    def __init__(self, result: ParsedScript | Exception) -> None:
        self._result = result

    async def parse(self, script_text: str, target_ms: int | None) -> ParsedScript:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


PARSED = ParsedScript(
    segmented=False,
    slides=(ParsedSlide(1, PRIVATE, (), (), None, None),),
    terms=(),
    request_id="req-audit",
)


def _ticket() -> ParseTicket:
    return ParseTicket(uuid.uuid4(), datetime.now(UTC), PRIVATE, 300000)


def test_result_save_failure_log_omits_script_text(monkeypatch, caplog):
    """결과 저장이 실패하면 예외 본문 대신 예외 종류·SQLSTATE·요청 ID 를 남기고 실패를 저장한다."""

    def fail(db, ticket, parsed):
        raise _db_error()

    monkeypatch.setattr(script_parse_runner.script_service, "complete_parse", fail)
    runner = ScriptParseRunner(_Parser(PARSED), session_factory=MagicMock)
    runner._save_failure = AsyncMock()
    ticket = _ticket()
    with caplog.at_level(logging.WARNING):
        asyncio.run(runner.run(ticket))
    assert PRIVATE not in caplog.text
    assert "대본 파싱 결과 저장 실패" in caplog.text
    assert f"script_version_id={ticket.script_version_id}" in caplog.text
    assert "request_id=req-audit error=OperationalError sqlstate=08006" in caplog.text
    runner._save_failure.assert_awaited_once_with(ticket, ScriptParseErrorCode.INTERNAL_ERROR)


def test_failure_save_failure_log_omits_script_text(monkeypatch, caplog):
    """FAILED 저장마저 실패해도 같은 규칙이다. 행은 PENDING 으로 남아 만료 뒤 재시도할 수 있다."""

    def fail(db, ticket, code):
        raise _db_error()

    monkeypatch.setattr(script_parse_runner.script_service, "fail_parse", fail)
    runner = ScriptParseRunner(_Parser(ValueError(PRIVATE)), session_factory=MagicMock)
    with caplog.at_level(logging.WARNING):
        asyncio.run(runner.run(_ticket()))
    assert PRIVATE not in caplog.text
    assert "code=INTERNAL_ERROR error=OperationalError sqlstate=08006" in caplog.text


def test_unexpected_parser_error_log_omits_message(caplog):
    """예상 못 한 예외의 메시지에는 AI 응답이나 대본 조각이 들어 있을 수 있다. 종류만 남긴다."""
    runner = ScriptParseRunner(_Parser(ValueError(PRIVATE)))
    runner._save_failure = AsyncMock()
    ticket = _ticket()
    with caplog.at_level(logging.WARNING):
        asyncio.run(runner.run(ticket))
    assert PRIVATE not in caplog.text
    assert "대본 파싱 중 예상 못 한 오류" in caplog.text
    assert "error=ValueError" in caplog.text
    runner._save_failure.assert_awaited_once_with(ticket, ScriptParseErrorCode.INTERNAL_ERROR)
