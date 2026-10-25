"""AI 오류와 재시도 로그에 사용자 원문을 남기지 않는다."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from pitch_coach_backend.module.pitch.dto import ParseTicket
from pitch_coach_backend.module.pitch.script_parse_runner import ScriptParseRunner
from pitch_coach_backend.module.pitch.script_parser import HttpScriptParser, ScriptParseError

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
