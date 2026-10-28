"""AI 장별 평가 기준 어댑터. DB 를 모른다 (script_parser.py 와 같은 자리).

    BE ──POST /scripts/rubric {pitch_id, script_version_id, slides, slide_numbers}──▶ AI
       ◀── rubrics · failed · meta (docs/ai/features/script-coverage-evaluation.md 5절)

평가 기준 본문은 BE 가 해석하지 않고 저장했다가 /takes/analyze · /takes/rescore 에 그대로 보낸다.
그래서 여기서는 장 번호(`meta.slide_number`)만 읽어 장별로 나눈다.

재시도는 여기서 하지 않는다. 버전마다 한 번인 재시도를 DB 에 기록한 뒤 보내야 하므로
(BE 가 재시작해도 다시 보내지 않게) 작업(script_rubric_runner)이 정한다. 여기서는 실패가
다시 부를 만한 것인지(`retryable`)만 알려 준다 — 무응답 · 408 · 429 · 5xx (architecture.md 3절).
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from pitch_coach_backend.module.pitch.adapter.script_parser import _error_code, _ssl_context

logger = logging.getLogger(__name__)

RUBRIC_PATH = "/scripts/rubric"
REQUEST_ID_HEADER = "X-Request-ID"

# 계약의 BE 시간 제한은 호출 한 번 130초다 (AI 예산 120초, architecture.md 3절).
# httpx 타임아웃은 단계별 상한이라 시도 한 번 전체를 ATTEMPT_TIMEOUT_SEC 로 묶는다
# (script_parser 와 같다)
DEFAULT_TIMEOUT = httpx.Timeout(130.0, connect=5.0)
ATTEMPT_TIMEOUT_SEC = 130.0
# 응답 meta.schema_version 의 major 가 이것과 다르면 필드 뜻이 바뀐 응답이라 읽지 않는다 (5절)
SCHEMA_MAJOR = "1"

# 최초 200 의 failed 중 이 코드인 장만 slide_numbers 로 한 번 다시 부른다.
# RUBRIC_MISSING 같은 입력 오류 장은 다시 불러도 같다 (architecture.md 1-7)
RETRYABLE_SLIDE_CODES = frozenset(
    {"LLM_TIMEOUT", "LLM_ERROR", "LLM_INVALID_OUTPUT", "LLM_UNAVAILABLE"}
)

_RETRYABLE_CLIENT_STATUSES = frozenset({408, 429})


@dataclass(frozen=True)
class RubricReply:
    # 장 번호 → 그 장의 평가 기준 (응답 rubrics[] 의 것 그대로)
    rubrics: dict[int, dict[str, Any]]
    # 장 번호 → 실패 코드 (error.code)
    failed: dict[int, str]
    # 응답의 meta {schema_version, feature_version, model}. 장마다 rubric_meta 로 같이 저장한다
    meta: dict[str, Any]


class RubricCallError(Exception):
    # detail 은 진단용이다. 작업 로그에는 이 문자열 대신 아래 코드만 남긴다.
    def __init__(
        self,
        detail: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        ai_error_code: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.retryable = retryable
        self.status_code = status_code
        self.ai_error_code = ai_error_code


class RubricClient(Protocol):
    """작업이 의존하는 최소 인터페이스. 테스트는 가짜로 바꿔 끼운다."""

    async def generate(
        self,
        *,
        pitch_id: str,
        script_version_id: str,
        slides: Sequence[tuple[int, str]],
        slide_numbers: Sequence[int] | None,
        request_id: str,
    ) -> RubricReply: ...


# ── AI 응답. extra 필드는 무시한다 (pydantic 기본) ─────────────────────────


class _WireError(BaseModel):
    code: str


class _WireFailed(BaseModel):
    slide_number: int
    error: _WireError


class _WireMeta(BaseModel):
    schema_version: str
    feature_version: str
    model: str | None = None


class _WireResult(BaseModel):
    rubrics: list[dict[str, Any]]
    failed: list[_WireFailed] = []
    meta: _WireMeta


def _slide_number(rubric: dict[str, Any]) -> int | None:
    meta = rubric.get("meta")
    number = meta.get("slide_number") if isinstance(meta, dict) else None
    # bool 은 int 의 하위 타입이라 type 으로 본다
    return number if type(number) is int else None


def _has_nul(value: Any) -> bool:
    # PostgreSQL JSONB 는 NUL 문자를 넣지 못해, 그대로 저장하면 그 응답의 저장이 통째로 실패한다.
    # 직렬화한 글에서 이스케이프를 찾지 않는다 — 대본에 글자 그대로 적힌 역슬래시 u0000 까지 걸린다
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(key) or _has_nul(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_nul(item) for item in value)
    return False


def to_reply(wire: _WireResult, targets: set[int]) -> RubricReply:
    """고른 장의 기준만 남긴다. 장 번호가 없거나 고르지 않은 장 · 겹친 장은 버린다."""
    major = wire.meta.schema_version.split(".", 1)[0]
    if major != SCHEMA_MAJOR:
        raise RubricCallError(f"모르는 schema_version {wire.meta.schema_version}", retryable=False)
    meta = wire.meta.model_dump()
    if _has_nul(meta):
        raise RubricCallError("meta 에 NUL 문자가 들어 있다", retryable=False)

    rubrics: dict[int, dict[str, Any]] = {}
    for rubric in wire.rubrics:
        number = _slide_number(rubric)
        if number is None or number not in targets or number in rubrics or _has_nul(rubric):
            logger.warning("평가 기준 한 장을 버림 slide_number=%s", number)
            continue
        rubrics[number] = rubric
    failed = {item.slide_number: item.error.code for item in wire.failed}
    return RubricReply(rubrics=rubrics, failed=failed, meta=meta)


class HttpRubricClient:
    """AI 서버를 HTTP 로 부른다. 호출마다 AsyncClient 를 새로 연다 (HttpScriptParser 와 같다)."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        attempt_timeout_sec: float = ATTEMPT_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._attempt_timeout_sec = attempt_timeout_sec
        # 테스트가 httpx.MockTransport 를 끼운다
        self._transport = transport

    async def generate(
        self,
        *,
        pitch_id: str,
        script_version_id: str,
        slides: Sequence[tuple[int, str]],
        slide_numbers: Sequence[int] | None,
        request_id: str,
    ) -> RubricReply:
        payload = {
            "pitch_id": pitch_id,
            "script_version_id": script_version_id,
            "slides": [{"slide_number": number, "script": script} for number, script in slides],
            "slide_numbers": None if slide_numbers is None else list(slide_numbers),
        }
        targets = {number for number, _ in slides} if slide_numbers is None else set(slide_numbers)
        try:
            async with asyncio.timeout(self._attempt_timeout_sec):
                response = await self._post(payload, request_id)
        except TimeoutError as exc:
            raise RubricCallError("시도 전체 시간 초과", retryable=True) from exc

        status = response.status_code
        if status >= 400:
            retryable = status >= 500 or status in _RETRYABLE_CLIENT_STATUSES
            code = _error_code(response)
            raise RubricCallError(
                f"status={status} ai_error_code={code}",
                retryable=retryable,
                status_code=status,
                ai_error_code=code,
            )
        try:
            wire = _WireResult.model_validate_json(response.content)
        except ValidationError as exc:
            # 계약이 다시 부르라고 한 경우(무응답 · 408 · 429 · 5xx)가 아니다
            raise RubricCallError(
                f"응답 스키마 불일치: {exc.error_count()}개 오류", retryable=False
            ) from exc
        return to_reply(wire, targets)

    async def _post(self, payload: dict[str, Any], request_id: str) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                verify=_ssl_context(),
            ) as client:
                return await client.post(
                    RUBRIC_PATH, json=payload, headers={REQUEST_ID_HEADER: request_id}
                )
        except httpx.TimeoutException as exc:
            raise RubricCallError(type(exc).__name__, retryable=True) from exc
        except httpx.HTTPError as exc:
            # 연결 실패 · 깨진 응답 등 응답을 제대로 받지 못한 경우는 모두 무응답으로 본다
            raise RubricCallError(type(exc).__name__, retryable=True) from exc
