"""AI 응답 본문 크기 상한 (`core/ai_response_limit.py`)."""

from collections.abc import AsyncIterator
from functools import partial

import httpx
import pytest

from pitch_coach_backend.core import ai_response_limit
from pitch_coach_backend.core.ai_response_limit import AI_RESPONSE_HOOKS, AiResponseTooLarge
from pitch_coach_backend.module.pitch.adapter.script_parser import (
    HttpScriptParser,
    ScriptParseError,
)
from pitch_coach_backend.module.pitch.dto import ScriptParseErrorCode
from pitch_coach_backend.module.take.service import coverage as coverage_service

LIMIT = 10

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def small_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_response_limit, "MAX_AI_RESPONSE_BYTES", LIMIT)


class _Chunks(httpx.AsyncByteStream):
    """Content-Length 없이 조각으로 오는 본문."""

    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


async def _post(response: httpx.Response) -> httpx.Response:
    transport = httpx.MockTransport(lambda request: response)
    async with httpx.AsyncClient(transport=transport, event_hooks=AI_RESPONSE_HOOKS) as client:
        return await client.post("http://ai/x")


async def test_body_within_the_limit_is_read_as_is() -> None:
    response = await _post(httpx.Response(200, stream=_Chunks(b"12345", b"67890")))

    assert response.content == b"1234567890"


async def test_content_length_over_the_limit_is_cut_before_reading() -> None:
    with pytest.raises(AiResponseTooLarge):
        await _post(httpx.Response(200, content=b"x" * (LIMIT + 1)))


async def test_body_without_content_length_is_cut_while_reading() -> None:
    """AI 가 조각으로 보내면 길이를 미리 알 수 없다. 읽은 양이 넘는 순간 끊는다."""
    with pytest.raises(AiResponseTooLarge):
        await _post(httpx.Response(200, stream=_Chunks(b"123456", b"789012")))


async def test_script_parser_treats_a_too_large_body_as_invalid_output() -> None:
    """`httpx.TransportError` 만 잡는 곳이라 따로 받는다."""
    parser = HttpScriptParser(
        "http://ai",
        attempts=1,
        retry_delay_sec=0,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 64)),
    )

    with pytest.raises(ScriptParseError) as caught:
        await parser.parse("x", None)

    assert caught.value.code is ScriptParseErrorCode.AI_INVALID_OUTPUT


async def test_coverage_treats_a_too_large_body_as_a_failed_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 64))
    monkeypatch.setattr(
        coverage_service.httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport)
    )

    with pytest.raises(coverage_service.CoverageCallError):
        await coverage_service.request_analyze({"take_id": "t"}, "id")
