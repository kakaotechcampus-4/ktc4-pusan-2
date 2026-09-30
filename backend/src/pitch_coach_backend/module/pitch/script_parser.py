"""AI 대본 파서 어댑터. DB 를 모른다 (auth/google.py, realtime/stt_adapter.py 와 같은 자리).

    BE ──POST /v1/scripts/parse {script_text}──▶ AI ──▶ SeperatedSlides

AI 는 노트북의 `SeperatedSlides` 모양을 그대로 돌려준다. 그 모양은 BE ↔ AI 사이 계약일 뿐이라
여기서 BE 의 `ParsedScript` 로 바꾼다 — AI 쪽 모양이 바뀌어도 고칠 곳이 이 파일 하나로 끝난다.

바꾸는 것
  status "success"/"fail"   → segmented True/False. AI 의 "fail" 은 "구분자가 없다"는 정상 결과라
                              우리 parse_status=FAILED(처리 실패) 와 이름이 겹친다
  slide_number null ("fail") → 1. script_slides.slide_number 가 NOT NULL 이다
  highlights ["0:6"]         → [(0, 6)]. FE 가 매번 문자열을 쪼개지 않게

LLM structured output 이라 스키마는 맞아도 값이 틀릴 수 있다. 그래서 구조가 깨진 응답
(슬라이드 없음·번호 중복)은 통째로 거절하고, 위치 하나가 틀린 highlight 는 그 항목만 버린다 —
강조 하나 때문에 슬라이드 분리 결과 전체를 버릴 이유는 없다.

재시도는 여기서 한다. 한 번 더 부르면 나을 수 있는 실패(타임아웃·5xx·계약 위반)만 다시 부르고,
AI 가 입력을 거절한 4xx 는 다시 불러도 같으니 바로 실패로 돌려준다.
"""

import asyncio
import logging
import re
import ssl
from dataclasses import dataclass
from functools import cache
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from pitch_coach_backend.module.pitch.dto import ScriptParseErrorCode

logger = logging.getLogger(__name__)

PARSE_PATH = "/v1/scripts/parse"

# AI 는 보통 3~5초. compose 내부망이라 연결은 금방 끝난다.
# httpx 의 타임아웃은 단계별(연결·바이트 사이 간격) 상한이라, AI 가 응답을 조금씩 흘리면
# 한 번의 호출이 얼마든지 길어질 수 있다. 그래서 시도 한 번 전체를 ATTEMPT_TIMEOUT_SEC 로 묶는다.
# 최악 소요 = 35 × 2 + 1 = 71초. 서비스의 만료 시간(PARSE_EXPIRE_AFTER, 90초)보다 짧아야
# "만료"로 보인 작업이 뒤에서 아직 도는 일이 없다 (tests/test_script_parser.py 가 확인한다)
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
ATTEMPT_TIMEOUT_SEC = 35.0
DEFAULT_ATTEMPTS = 2
DEFAULT_RETRY_DELAY_SEC = 1.0

# 4xx 인데 다시 부르면 나을 수 있는 것: 408 Request Timeout, 429 Too Many Requests
_RETRYABLE_CLIENT_STATUSES = frozenset({408, 429})
_HIGHLIGHT_RE = re.compile(r"^(\d+):(\d+)$")
# 로그에 AI 응답 본문을 남길 때의 상한. 사용자 대본이 되돌아올 수 있어 짧게 자른다
_LOG_BODY_LIMIT = 200
# script_slides.slide_number 는 PostgreSQL integer(int4). 넘으면 저장에서 터지므로 받을 때 거절한다
_MAX_SLIDE_NUMBER = 2_147_483_647


@cache
def _ssl_context() -> ssl.SSLContext:
    """httpx 는 클라이언트를 만들 때마다 CA 번들을 읽어 SSL 컨텍스트를 만든다 (실측 약 9ms).

    그 일이 STT 와 같은 이벤트 루프에서 일어나므로 한 번만 만들어 재사용한다.
    인증서 검증은 그대로 켜 둔다 (AI_BASE_URL 을 https 로 바꿔도 안전하게).
    """
    return httpx.create_ssl_context()


@dataclass(frozen=True)
class ParsedSlide:
    slide_number: int
    content: str
    keywords: tuple[str, ...]
    # content 안의 [start, end) 위치
    highlights: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class ParsedScript:
    segmented: bool
    slides: tuple[ParsedSlide, ...]
    terms: tuple[str, ...]


class ScriptParseError(Exception):
    # detail 은 로그용 메시지에만 들어간다 (FE 에는 code 만 나간다)
    def __init__(self, code: ScriptParseErrorCode, detail: str, *, retryable: bool) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.retryable = retryable


class ScriptParser(Protocol):
    """파싱 작업이 의존하는 최소 인터페이스. 테스트는 가짜로 바꿔 끼운다."""

    async def parse(self, script_text: str) -> ParsedScript: ...


# ── AI 응답 (SeperatedSlides) ──────────────────────────────────────────
# extra 필드는 무시한다 (pydantic 기본). AI 가 필드를 더해도 BE 가 깨지지 않게


class _WireSlide(BaseModel):
    slide_number: int | None
    script: str
    keywords: list[str]
    highlights: list[str] = []


class _WireResult(BaseModel):
    status: Literal["success", "fail"]
    slides: list[_WireSlide]
    terms: list[str] = []


def _highlights(raw: list[str], content: str) -> tuple[tuple[int, int], ...]:
    """'start:end' 중 content 안을 가리키는 것만 남긴다.

    노트북은 키워드가 본문에 없으면 find() 의 -1 로 "-1:5" 를 만들고, 시스템 프롬프트 없이
    부르면 LLM 이 위치 대신 단어("멸종위기청년")를 넣기도 했다. 둘 다 여기서 걸러진다.
    """
    spans: list[tuple[int, int]] = []
    for item in raw:
        match = _HIGHLIGHT_RE.match(item.strip())
        if match is None:
            continue
        start, end = int(match[1]), int(match[2])
        if start < end <= len(content):
            spans.append((start, end))
    return tuple(spans)


def _terms(raw: list[str]) -> tuple[str, ...]:
    # 순서는 유지하고 빈 값·중복만 뺀다 (앞쪽이 STT 오인식 가능성이 높은 용어다)
    return tuple(dict.fromkeys(term.strip() for term in raw if term.strip()))


def _invalid(detail: str) -> ScriptParseError:
    # LLM 출력이 틀린 경우라 다시 부르면 맞을 수 있다
    return ScriptParseError(ScriptParseErrorCode.AI_INVALID_OUTPUT, detail, retryable=True)


def _has_nul(wire: _WireResult) -> bool:
    # PostgreSQL 은 TEXT 에 NUL(\x00) 을, JSONB 에 \u0000 을 넣지 못한다. 그대로 저장하면
    # DataError 로 저장이 통째로 실패하니 받는 단계에서 거절한다
    texts = list(wire.terms)
    for slide in wire.slides:
        texts.append(slide.script)
        texts.extend(slide.keywords)
    return any("\x00" in text for text in texts)


def to_parsed_script(wire: _WireResult) -> ParsedScript:
    segmented = wire.status == "success"

    if _has_nul(wire):
        raise _invalid("NUL 문자가 들어 있다")

    if segmented:
        if not wire.slides:
            raise _invalid("success 인데 slides 가 비었다")
        numbers = [slide.slide_number for slide in wire.slides]
        if any(number is None or not 1 <= number <= _MAX_SLIDE_NUMBER for number in numbers):
            raise _invalid(f"success 인데 slide_number 가 올바르지 않다: {numbers}")
        if len(set(numbers)) != len(numbers):
            raise _invalid(f"slide_number 가 겹친다: {numbers}")
    elif len(wire.slides) != 1:
        raise _invalid(f"fail 인데 slides 가 {len(wire.slides)}개다")

    slides = tuple(
        ParsedSlide(
            # fail 이면 AI 는 null 을 준다. 대본 전체가 한 장이므로 1번
            slide_number=slide.slide_number if segmented else 1,
            content=slide.script,
            keywords=tuple(slide.keywords),
            highlights=_highlights(slide.highlights, slide.script),
        )
        for slide in wire.slides
    )
    return ParsedScript(segmented=segmented, slides=slides, terms=_terms(wire.terms))


class HttpScriptParser:
    """AI 서버를 HTTP 로 부른다.

    호출마다 AsyncClient 를 새로 연다. 업로드 한 번에 한 번 부르는 정도라 커넥션을 재사용해
    얻는 것이 거의 없고, 대신 앱 수명(lifespan)에 클라이언트를 묶을 필요가 없어진다.
    만드는 비용에서 큰 몫(SSL 컨텍스트)은 _ssl_context() 로 한 번만 치른다.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        attempt_timeout_sec: float = ATTEMPT_TIMEOUT_SEC,
        attempts: int = DEFAULT_ATTEMPTS,
        retry_delay_sec: float = DEFAULT_RETRY_DELAY_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts 는 1 이상이어야 합니다.")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._attempt_timeout_sec = attempt_timeout_sec
        self._attempts = attempts
        self._retry_delay_sec = retry_delay_sec
        # 테스트가 httpx.MockTransport 를 끼운다
        self._transport = transport

    async def parse(self, script_text: str) -> ParsedScript:
        for attempt in range(1, self._attempts + 1):
            try:
                return await self._attempt(script_text)
            except ScriptParseError as exc:
                if not exc.retryable or attempt == self._attempts:
                    raise
                logger.warning("AI 대본 파싱 %d차 실패, 다시 시도: %s", attempt, exc)
                await asyncio.sleep(self._retry_delay_sec)
        raise AssertionError("unreachable")  # attempts >= 1 이면 위에서 반드시 끝난다

    async def _attempt(self, script_text: str) -> ParsedScript:
        """시도 한 번. 전체를 ATTEMPT_TIMEOUT_SEC 로 묶는다 (httpx 타임아웃은 단계별이라)."""
        try:
            async with asyncio.timeout(self._attempt_timeout_sec):
                return await self._parse_once(script_text)
        except TimeoutError as exc:
            raise ScriptParseError(
                ScriptParseErrorCode.AI_TIMEOUT, "시도 전체 시간 초과", retryable=True
            ) from exc

    async def _parse_once(self, script_text: str) -> ParsedScript:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                verify=_ssl_context(),
            ) as client:
                response = await client.post(PARSE_PATH, json={"script_text": script_text})
        except httpx.TimeoutException as exc:
            raise ScriptParseError(
                ScriptParseErrorCode.AI_TIMEOUT, type(exc).__name__, retryable=True
            ) from exc
        except httpx.TransportError as exc:
            # 연결 거부·DNS 실패 등. AI 컨테이너가 재시작 중일 수 있어 한 번 더 본다
            raise ScriptParseError(
                ScriptParseErrorCode.AI_UNAVAILABLE, f"{type(exc).__name__}: {exc}", retryable=True
            ) from exc

        status = response.status_code
        if status >= 400:
            body = response.text[:_LOG_BODY_LIMIT]
            if status < 500 and status not in _RETRYABLE_CLIENT_STATUSES:
                raise ScriptParseError(
                    ScriptParseErrorCode.AI_REJECTED, f"{status} {body}", retryable=False
                )
            raise ScriptParseError(
                ScriptParseErrorCode.AI_ERROR, f"{status} {body}", retryable=True
            )

        try:
            wire = _WireResult.model_validate_json(response.content)
        except ValidationError as exc:
            raise _invalid(f"응답 스키마 불일치: {exc.error_count()}개 오류") from exc
        return to_parsed_script(wire)
