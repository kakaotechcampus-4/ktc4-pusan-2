"""AI 대본 파서 어댑터. 실제 httpx 요청을 만들고 MockTransport 로 AI 응답만 바꿔 끼운다.

응답 모양은 AI 노트북(script_slide_analysis.ipynb) 의 SeperatedSlides 그대로다.
"""

import asyncio
import json
from typing import Any

import httpx
import pytest

from pitch_coach_backend.module.pitch import service
from pitch_coach_backend.module.pitch.dto import ScriptParseErrorCode
from pitch_coach_backend.module.pitch.script_parser import (
    ATTEMPT_TIMEOUT_SEC,
    DEFAULT_ATTEMPTS,
    DEFAULT_RETRY_DELAY_SEC,
    PARSE_PATH,
    HttpScriptParser,
    ScriptParseError,
)

BASE_URL = "http://ai.test"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _slide(number: int | None, script: str, highlights: list[str] | None = None) -> dict:
    return {
        "slide_number": number,
        "script": script,
        "keywords": ["발표"],
        "highlights": highlights or [],
    }


def _body(status: str = "success", slides: list[dict] | None = None, **extra: Any) -> dict:
    return {
        "status": status,
        "slides": slides if slides is not None else [_slide(1, "안녕하세요. 발표를 시작합니다.")],
        "terms": [],
        **extra,
    }


class Recorder:
    """AI 역할. 응답을 차례로 돌려주고 받은 요청을 남긴다."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _parser(recorder: Recorder, attempts: int = 2) -> HttpScriptParser:
    return HttpScriptParser(
        BASE_URL,
        attempts=attempts,
        retry_delay_sec=0,
        transport=httpx.MockTransport(recorder),
    )


async def test_sends_script_text_and_converts_success() -> None:
    content = "안녕하세요. 멸종위기청년에 대해 발표하겠습니다."
    recorder = Recorder(
        httpx.Response(
            200,
            json=_body(
                slides=[_slide(1, content, ["7:13"]), _slide(3, "감사합니다.")],
                terms=["멸종위기청년", "SeatFlow"],
            ),
        )
    )

    parsed = await _parser(recorder).parse("원문 대본")

    [request] = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}{PARSE_PATH}"
    assert json.loads(request.content) == {"script_text": "원문 대본"}

    assert parsed.segmented is True
    # 원문 번호를 그대로 쓴다 (1, 3)
    assert [slide.slide_number for slide in parsed.slides] == [1, 3]
    assert parsed.slides[0].highlights == ((7, 13),)
    assert content[7:13] == "멸종위기청년"
    assert parsed.terms == ("멸종위기청년", "SeatFlow")


async def test_fail_status_is_an_unsegmented_success() -> None:
    """AI 의 status="fail" 은 처리 실패가 아니라 "구분자 없음". 한 장짜리 결과로 받는다."""
    recorder = Recorder(httpx.Response(200, json=_body("fail", [_slide(None, "대본 전체")])))

    parsed = await _parser(recorder).parse("대본 전체")

    assert parsed.segmented is False
    [slide] = parsed.slides
    assert slide.slide_number == 1  # AI 는 null. slide_number 가 NOT NULL 이라 1
    assert slide.content == "대본 전체"


async def test_broken_highlights_are_dropped_one_by_one() -> None:
    content = "0123456789"
    highlights = [
        "0:3",  # 정상
        "-1:5",  # 노트북: 키워드가 본문에 없으면 find() 가 -1
        "멸종위기청년",  # 시스템 프롬프트 없이 불렀을 때 LLM 이 위치 대신 단어를 넣음
        "5:5",  # 빈 구간
        "8:11",  # 본문 밖
        " 4:6 ",  # 공백은 허용
    ]
    recorder = Recorder(httpx.Response(200, json=_body(slides=[_slide(1, content, highlights)])))

    parsed = await _parser(recorder).parse("x")

    assert parsed.slides[0].highlights == ((0, 3), (4, 6))


async def test_terms_drop_blanks_and_duplicates_keeping_order() -> None:
    recorder = Recorder(httpx.Response(200, json=_body(terms=["B", " ", "A", "B", " A "])))

    parsed = await _parser(recorder).parse("x")

    assert parsed.terms == ("B", "A")


async def test_extra_fields_from_ai_are_ignored() -> None:
    recorder = Recorder(httpx.Response(200, json=_body(model="gpt", elapsed_ms=3100)))

    parsed = await _parser(recorder).parse("x")

    assert parsed.segmented is True


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(_body(slides=[]), id="success-without-slides"),
        pytest.param(_body(slides=[_slide(1, "a"), _slide(1, "b")]), id="duplicate-number"),
        pytest.param(_body(slides=[_slide(None, "a")]), id="success-with-null-number"),
        pytest.param(_body(slides=[_slide(0, "a")]), id="non-positive-number"),
        # DB 의 integer(int4) 를 넘는 번호. 저장에서 터지기 전에 거절한다
        pytest.param(_body(slides=[_slide(2**31, "a")]), id="number-over-int4"),
        pytest.param(_body("fail", [_slide(None, "a"), _slide(None, "b")]), id="fail-many"),
        pytest.param({"status": "maybe", "slides": []}, id="unknown-status"),
        pytest.param({"slides": "nope"}, id="schema-mismatch"),
        # PostgreSQL 이 TEXT·JSONB 에 넣지 못하는 NUL. 저장에서 터지기 전에 여기서 거절한다
        pytest.param(_body(slides=[_slide(1, "a\x00b")]), id="nul-in-script"),
        pytest.param(
            _body(slides=[{**_slide(1, "ab"), "keywords": ["a\x00"]}]), id="nul-in-keyword"
        ),
        pytest.param(_body(terms=["x\x00"]), id="nul-in-term"),
    ],
)
async def test_contract_violations_are_invalid_output(body: dict) -> None:
    recorder = Recorder(httpx.Response(200, json=body), httpx.Response(200, json=body))

    with pytest.raises(ScriptParseError) as caught:
        await _parser(recorder).parse("x")

    assert caught.value.code is ScriptParseErrorCode.AI_INVALID_OUTPUT
    # LLM 출력은 다시 부르면 맞을 수 있어 한 번 더 부른다
    assert len(recorder.requests) == 2


async def test_non_json_body_is_invalid_output() -> None:
    recorder = Recorder(httpx.Response(200, text="<html>502</html>"))

    with pytest.raises(ScriptParseError) as caught:
        await _parser(recorder, attempts=1).parse("x")

    assert caught.value.code is ScriptParseErrorCode.AI_INVALID_OUTPUT


async def test_client_error_is_rejected_without_retry() -> None:
    recorder = Recorder(httpx.Response(422, json={"error": {"code": "EMPTY_SCRIPT"}}))

    with pytest.raises(ScriptParseError) as caught:
        await _parser(recorder).parse("x")

    assert caught.value.code is ScriptParseErrorCode.AI_REJECTED
    assert caught.value.retryable is False
    # 입력 문제는 다시 불러도 같다
    assert len(recorder.requests) == 1


@pytest.mark.parametrize("status", [500, 503, 429, 408])
async def test_server_errors_are_retried_once(status: int) -> None:
    recorder = Recorder(httpx.Response(status), httpx.Response(status))

    with pytest.raises(ScriptParseError) as caught:
        await _parser(recorder).parse("x")

    assert caught.value.code is ScriptParseErrorCode.AI_ERROR
    assert len(recorder.requests) == 2


async def test_retry_succeeds_after_one_server_error() -> None:
    recorder = Recorder(httpx.Response(503), httpx.Response(200, json=_body()))

    parsed = await _parser(recorder).parse("x")

    assert parsed.segmented is True
    assert len(recorder.requests) == 2


async def test_timeout_is_ai_timeout() -> None:
    recorder = Recorder(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))

    with pytest.raises(ScriptParseError) as caught:
        await _parser(recorder).parse("x")

    assert caught.value.code is ScriptParseErrorCode.AI_TIMEOUT
    assert len(recorder.requests) == 2


async def test_connection_failure_is_ai_unavailable() -> None:
    recorder = Recorder(httpx.ConnectError("refused"), httpx.ConnectError("refused"))

    with pytest.raises(ScriptParseError) as caught:
        await _parser(recorder).parse("x")

    assert caught.value.code is ScriptParseErrorCode.AI_UNAVAILABLE


async def test_base_url_trailing_slash_is_tolerated() -> None:
    recorder = Recorder(httpx.Response(200, json=_body()))
    parser = HttpScriptParser(
        f"{BASE_URL}/", retry_delay_sec=0, transport=httpx.MockTransport(recorder)
    )

    await parser.parse("x")

    assert str(recorder.requests[0].url) == f"{BASE_URL}{PARSE_PATH}"


async def test_slow_trickling_response_hits_the_attempt_timeout() -> None:
    """httpx 타임아웃은 단계별이라 조금씩 흘리는 응답은 못 끊는다. 시도 전체 상한이 끊는다."""
    calls = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return httpx.Response(200, json=_body())

    parser = HttpScriptParser(
        BASE_URL,
        attempt_timeout_sec=0.05,
        retry_delay_sec=0,
        transport=httpx.MockTransport(slow),
    )

    with pytest.raises(ScriptParseError) as caught:
        await parser.parse("x")

    assert caught.value.code is ScriptParseErrorCode.AI_TIMEOUT
    assert calls == 2  # 타임아웃은 재시도 대상


def test_worst_case_parse_time_is_shorter_than_expiry() -> None:
    """최악 소요가 만료보다 길면, 아직 도는 작업이 PARSE_EXPIRED 로 보이고 재시도가 열린다."""
    worst = ATTEMPT_TIMEOUT_SEC * DEFAULT_ATTEMPTS + DEFAULT_RETRY_DELAY_SEC * (
        DEFAULT_ATTEMPTS - 1
    )
    assert worst < service.PARSE_EXPIRE_AFTER.total_seconds()
