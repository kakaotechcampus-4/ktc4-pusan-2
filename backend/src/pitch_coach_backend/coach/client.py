"""AI 실시간 코치 호출. DB 를 모른다 (module/pitch/adapter/script_parser.py 와 같은 자리).

    BE ──POST /coach/plan      (백그라운드, LLM)       ──▶ AI
    BE ──POST /coach/evaluate  (발표 중 metrics 마다)  ──▶ AI
    BE ──POST /coach/finalize  (Take 종료 뒤 백그라운드) ──▶ AI

계약은 `docs/ai/features/coach-agent.md` 5 · 8절, 공통 규칙은 `docs/ai/architecture.md` 2~5절이다.
AI 응답을 BE 형식으로 바꾸는 곳은 이 파일 하나다(architecture.md 2절). AI 응답 모양이 바뀌면
여기만 고친다.

응답 모양과 받은 그대로 두는 칸(`coach_state` · `events` · `take_result` · `coaching_plan` ·
`meta`)은 `replies.py` 다. 여기서는 부르고, 다시 부르고, 응답을 그 모양으로 읽는다.

호출 규칙 (coach-agent.md 8절)
  - evaluate: 300ms, 다시 부르지 않는다. 늦은 응답은 버린다 — 다음 1초 요청이 대신한다
  - finalize: 10초, `replay` 를 붙인 호출만 60초. 409 `REPLAY_REQUIRED` 는 `ReplayRequired` 로
    돌려준다. `replay` 를 만들어 다시 부르는 건 호출자 몫이다(원자료가 DB 에 있다)
  - plan: 30초. 200 + `fallback_reason` 도 정상 결과다
  - plan · finalize 는 일시 오류(연결 실패 · 시간 초과 · 408 · 429 · 5xx)면 한 번 다시 부른다.
    그 밖의 4xx 는 다시 불러도 같다
  - plan · finalize 는 다른 백그라운드 AI 호출과 합쳐 동시 개수를 센다 (`core/ai_slots.py`,
    architecture.md 3절). 긴 요청이 AI 스레드 풀을 채우면 1초마다 오는 evaluate 가 300ms 를 넘긴다
  - 요청마다 `X-Request-ID`. 다시 부를 때도 같은 값이다 — AI 로그에서 두 시도가 한 요청으로 묶인다
  - 로그에는 상태 코드와 `error.code` 만 남긴다. 본문에는 대본 · 전사가 되돌아올 수 있다

실패는 던지지 않고 None 을 돌려준다. 코칭이 안 된다고 발표가 멈추면 안 되고, 호출자가 할 수 있는
일도 "이번 1초는 건너뛴다" · "Take 를 FAILED 로 둔다" 정도다.
"""

import asyncio
import json
import logging
import ssl
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from pitch_coach_backend.coach.replies import (
    MAX_T_MS,
    EvaluateReply,
    FinalizeReply,
    PlanReply,
    ReplayRequired,
    Reply,
)
from pitch_coach_backend.core import ai_slots
from pitch_coach_backend.core.ai_response_limit import AI_RESPONSE_HOOKS

logger = logging.getLogger(__name__)

EVALUATE_PATH = "/coach/evaluate"

FINALIZE_PATH = "/coach/finalize"

PLAN_PATH = "/coach/plan"


# 시도 하나 전체의 상한. httpx 타임아웃은 단계별(연결 · 바이트 사이 간격) 상한이라 따로 묶는다
EVALUATE_TIMEOUT_SEC = 0.3

FINALIZE_TIMEOUT_SEC = 10.0


# replay 는 Take 길이만큼 다시 판정한다. 한 시간 Take 가 약 15초 (coach-agent.md 8절)
FINALIZE_REPLAY_TIMEOUT_SEC = 60.0


# AI 예산 25초 + 스레드 대기 · 네트워크 여유 (architecture.md 3절)
PLAN_TIMEOUT_SEC = 30.0

BACKGROUND_ATTEMPTS = 2

RETRY_DELAY_SEC = 1.0


# AI 가 없을 때(연결 못 함 · 경로 없음 404 · 준비 중 503) evaluate 를 쉬는 시간. 프로세스 전체에
# 건다. 쉬지 않으면 Take 마다 1초에 한 번씩 죽은 서버를 두드린다. 시선 · 음량 창(30초)보다 짧아야
# AI 가 돌아왔을 때 빠진 1초 없이 이어진다 — 쉬는 동안의 1초 기록은 다음 요청의 창에 실린다
EVALUATE_PAUSE_SEC = 10.0


# 같은 evaluate 실패 경고를 다시 남기는 간격. 매초 오는 요청이라 그대로 두면 로그가 덮인다
EVALUATE_WARN_INTERVAL_SEC = 60.0


# 이 major 버전의 응답만 읽는다. major 가 다르면 필드 이름 · 뜻이 바뀐 것이라(architecture.md 5절)
# 이 코드로 읽으면 엉뚱한 값을 저장하거나 FE 에 보낸다
SCHEMA_MAJOR = "1"

REQUEST_ID_HEADER = "X-Request-ID"

REPLAY_REQUIRED_CODE = "REPLAY_REQUIRED"


# 응답 본문이 이보다 크면 남긴다 (coach-agent.md 12-2 "잰 크기보다 크게 벗어나면 로그"). 잰 값:
# coach_state 약 16KB · 이벤트 하나 0.3~0.4KB, take_result 약 6.7KB(92초 · 3장, 장 · 문제 구간 ·
# 개입 수에 비례), 코칭 계획 수 KB. 대략 네 배 이상이면 남긴다
_LARGE_BODY_BYTES = {EVALUATE_PATH: 64_000, FINALIZE_PATH: 256_000, PLAN_PATH: 64_000}


# 4xx 인데 다시 부르면 나을 수 있는 것: 408 Request Timeout, 429 Too Many Requests
_RETRYABLE_CLIENT_STATUSES = frozenset({408, 429})


# AI 가 아직 없다는 뜻의 상태 코드. 404 는 그 경로가 배포 전, 503 은 시작 중(`NOT_READY`)이다
_UNAVAILABLE_STATUSES = frozenset({404, 503})


@cache
def _ssl_context() -> ssl.SSLContext:
    # 클라이언트마다 CA 번들을 다시 읽지 않게 한 번만 만든다 (script_parser.py 와 같은 이유)
    return httpx.create_ssl_context()


class CoachClient(Protocol):
    """코치를 부르는 쪽이 의존하는 최소 인터페이스. 테스트는 가짜로 바꿔 끼운다."""

    async def evaluate(self, request: dict[str, Any]) -> EvaluateReply | None: ...

    async def finalize(self, request: dict[str, Any]) -> FinalizeReply | ReplayRequired | None: ...

    async def plan(self, request: dict[str, Any]) -> PlanReply | None: ...


# ── HTTP ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _Endpoint:
    path: str
    attempts: int
    # 동시 개수 제한(`ai_slots.background`)을 받는 백그라운드 호출인가
    background: bool


class _Failure(Exception):
    """시도 하나가 실패했다. `retryable` 이면 다시 불러 볼 만하고, `unavailable` 이면 AI 가 없다."""

    def __init__(self, reason: str, *, retryable: bool, unavailable: bool = False) -> None:
        super().__init__(reason)
        self.retryable = retryable
        self.unavailable = unavailable


class HttpCoachClient:
    """AI 서버를 HTTP 로 부른다.

    호출마다 AsyncClient 를 새로 연다 (HttpScriptParser 와 같다). compose 내부망이라 연결은
    금방이고, 앱 수명에 클라이언트를 묶지 않아도 된다. evaluate 쉬기는 이 객체가 들고 있으므로
    프로세스에 하나만 만든다 (`coach/dependencies.py`).
    """

    def __init__(
        self,
        base_url: str,
        *,
        evaluate_timeout_sec: float = EVALUATE_TIMEOUT_SEC,
        finalize_timeout_sec: float = FINALIZE_TIMEOUT_SEC,
        replay_timeout_sec: float = FINALIZE_REPLAY_TIMEOUT_SEC,
        plan_timeout_sec: float = PLAN_TIMEOUT_SEC,
        attempts: int = BACKGROUND_ATTEMPTS,
        retry_delay_sec: float = RETRY_DELAY_SEC,
        evaluate_pause_sec: float = EVALUATE_PAUSE_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._evaluate_timeout_sec = evaluate_timeout_sec
        self._finalize_timeout_sec = finalize_timeout_sec
        self._replay_timeout_sec = replay_timeout_sec
        self._plan_timeout_sec = plan_timeout_sec
        self._evaluate = _Endpoint(EVALUATE_PATH, attempts=1, background=False)
        self._finalize = _Endpoint(FINALIZE_PATH, attempts=attempts, background=True)
        self._plan = _Endpoint(PLAN_PATH, attempts=attempts, background=True)
        self._retry_delay_sec = retry_delay_sec
        self._evaluate_pause_sec = evaluate_pause_sec
        self._evaluate_paused_until = 0.0
        # 경고 종류 → 다음에 남길 수 있는 시각, 그 사이 건너뛴 개수
        self._evaluate_warned: dict[str, tuple[float, int]] = {}
        # 테스트가 httpx.MockTransport 를 끼운다
        self._transport = transport
        self._clock = clock

    async def evaluate(self, request: dict[str, Any]) -> EvaluateReply | None:
        if not _t_ms_in_range(request):
            # AI 가 422 로 거절한다. Take 자동 종료(최대 1시간)가 지켜지면 나오지 않는다
            self._warn_evaluate(
                "t_ms",
                "t_ms 가 범위 밖이라 evaluate 를 부르지 않는다 %s t_ms=%s",
                _subject(request),
                request.get("t_ms"),
            )
            return None
        if self._clock() < self._evaluate_paused_until:
            # AI 가 없다. 그 1초는 건너뛴다 — 쉬는 동안의 기록은 다음 요청의 창에 실린다
            return None
        try:
            reply = await self._call(
                self._evaluate, request, EvaluateReply, self._evaluate_timeout_sec
            )
        except _Failure as exc:
            if exc.unavailable:
                self._evaluate_paused_until = self._clock() + self._evaluate_pause_sec
                self._warn_evaluate(
                    "unavailable",
                    "AI 코치가 없어 %.0f초 동안 evaluate 를 쉰다: %s",
                    self._evaluate_pause_sec,
                    exc,
                )
            elif not exc.retryable:
                # 422 · 스키마 불일치처럼 다시 불러도 같은 것. BE 와 AI 의 계약이 어긋났다
                self._warn_evaluate(str(exc), "evaluate 실패: %s", exc)
            else:
                # 시간 초과 · 5xx. 그 1초만 건너뛴다. 계속되면 코칭이 멈춘 것이라 묶어서 남긴다
                self._warn_evaluate("transient", "evaluate 가 제때 답하지 못했다: %s", exc)
            return None
        assert isinstance(reply, EvaluateReply)
        return reply

    async def finalize(self, request: dict[str, Any]) -> FinalizeReply | ReplayRequired | None:
        if not _t_ms_in_range(request):
            logger.warning(
                "t_ms 가 범위 밖이라 finalize 를 부르지 않는다 %s t_ms=%s",
                _subject(request),
                request.get("t_ms"),
            )
            return None
        replay = request.get("replay") is not None
        timeout_sec = self._replay_timeout_sec if replay else self._finalize_timeout_sec
        try:
            reply = await self._call(self._finalize, request, FinalizeReply, timeout_sec)
        except _Failure:
            return None
        if isinstance(reply, ReplayRequired) and replay:
            # 원자료를 다 보냈는데도 못 셌다. 다시 불러도 같다 (8절: replay 를 붙여도 409 면 실패)
            logger.warning("replay 를 붙였는데도 REPLAY_REQUIRED 다 %s", _subject(request))
            return None
        return reply

    async def plan(self, request: dict[str, Any]) -> PlanReply | None:
        try:
            reply = await self._call(self._plan, request, PlanReply, self._plan_timeout_sec)
        except _Failure:
            return None
        assert isinstance(reply, PlanReply)
        if reply.fallback_reason is not None:
            logger.info(
                "코칭 계획이 기본 계획으로 돌아갔다 pitch=%s reason=%s",
                request.get("pitch_id"),
                reply.fallback_reason,
            )
        return reply

    # ── 내부 ──────────────────────────────────────────────────────────

    async def _call[T: Reply](
        self, endpoint: _Endpoint, request: dict[str, Any], reply_type: type[T], timeout_sec: float
    ) -> T | ReplayRequired:
        """정해진 횟수만큼 부른다. 끝내 실패하면 마지막 `_Failure` 를 던진다."""
        try:
            # httpx 의 json= 과 같은 규칙이다. 직접 만드는 건 실패를 잡으려고다 — UUID 같은 값이나
            # NaN 이 섞이면 TypeError · ValueError 가 나는데, 새면 부른 쪽(발표 중 루프)이 멈춘다
            body = json.dumps(
                request, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode()
        except (TypeError, ValueError) as exc:
            logger.error(
                "코치 요청을 JSON 으로 만들 수 없다 path=%s %s: %s",
                endpoint.path,
                _subject(request),
                type(exc).__name__,
            )
            raise _Failure(f"요청 직렬화 실패 {type(exc).__name__}", retryable=False) from exc
        # 다시 불러도 같은 ID 다. AI 로그에서 한 요청의 두 시도가 묶여 보인다
        request_id = uuid.uuid4().hex
        for attempt in range(1, endpoint.attempts + 1):
            try:
                if endpoint.background:
                    # 자리를 기다린 시간은 시간 제한에 넣지 않는다
                    async with ai_slots.background():
                        return await self._attempt(
                            endpoint, request, body, reply_type, timeout_sec, request_id
                        )
                return await self._attempt(
                    endpoint, request, body, reply_type, timeout_sec, request_id
                )
            except _Failure as exc:
                last = attempt == endpoint.attempts or not exc.retryable
                if endpoint is not self._evaluate:
                    # evaluate 는 매초 오므로 evaluate() 가 묶어서 남긴다
                    logger.warning(
                        "코치 호출 실패 path=%s %s request_id=%s attempt=%d/%d: %s",
                        endpoint.path,
                        _subject(request),
                        request_id,
                        attempt,
                        endpoint.attempts,
                        exc,
                    )
                if last:
                    raise
                await asyncio.sleep(self._retry_delay_sec)
        raise AssertionError("unreachable")

    async def _attempt[T: Reply](
        self,
        endpoint: _Endpoint,
        request: dict[str, Any],
        body: bytes,
        reply_type: type[T],
        timeout_sec: float,
        request_id: str,
    ) -> T | ReplayRequired:
        try:
            async with (
                asyncio.timeout(timeout_sec),
                httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=timeout_sec,
                    transport=self._transport,
                    verify=_ssl_context(),
                    event_hooks=AI_RESPONSE_HOOKS,
                ) as client,
            ):
                response = await client.post(
                    endpoint.path,
                    content=body,
                    headers={"Content-Type": "application/json", REQUEST_ID_HEADER: request_id},
                )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise _Failure("시간 초과", retryable=True) from exc
        except httpx.ConnectError as exc:
            # 연결 거부 · DNS 실패. AI 컨테이너가 없거나 재시작 중이다
            raise _Failure(type(exc).__name__, retryable=True, unavailable=True) from exc
        except (httpx.UnsupportedProtocol, httpx.InvalidURL) as exc:
            # AI_BASE_URL 이 틀렸다. 다시 불러도 같다
            raise _Failure(type(exc).__name__, retryable=False) from exc
        except httpx.TransportError as exc:
            # 연결은 됐는데 주고받다 끊겼다. 그 요청 하나의 일이라 AI 가 없다고 보지 않는다
            raise _Failure(type(exc).__name__, retryable=True) from exc
        except httpx.HTTPError as exc:
            raise _Failure(type(exc).__name__, retryable=False) from exc

        status = response.status_code
        if status >= 400:
            code = _error_code(response)
            if status == 409 and code == REPLAY_REQUIRED_CODE and endpoint.path == FINALIZE_PATH:
                return ReplayRequired()
            raise _Failure(
                f"status={status} code={code}",
                retryable=status >= 500 or status in _RETRYABLE_CLIENT_STATUSES,
                unavailable=status in _UNAVAILABLE_STATUSES,
            )

        size = len(response.content)
        if size > _LARGE_BODY_BYTES[endpoint.path]:
            logger.warning(
                "코치 응답이 크다 path=%s %s request_id=%s bytes=%d",
                endpoint.path,
                _subject(request),
                request_id,
                size,
            )
        try:
            reply = reply_type.model_validate_json(response.content)
        except ValidationError as exc:
            # 다시 불러도 같은 모양이 온다. 오류 위치만 남긴다 (값에 대본 · 전사가 있을 수 있다)
            where = ",".join(".".join(map(str, e["loc"])) for e in exc.errors()[:3])
            raise _Failure(f"응답 모양이 계약과 다르다 at={where}", retryable=False) from exc
        if isinstance(reply, FinalizeReply) and (
            reply.take_result["take_id"] != request.get("take_id")
            or reply.take_result["duration_ms"] != request.get("t_ms")
        ):
            # 다른 Take · 종료 시각의 결과를 저장하면 원자료까지 삭제될 수 있다.
            raise _Failure("finalize 응답이 요청한 Take · 종료 시각과 다르다", retryable=False)
        version = str(reply.meta["schema_version"])
        if version.split(".", 1)[0] != SCHEMA_MAJOR:
            raise _Failure(f"모르는 schema_version={version}", retryable=False)
        return reply

    def _warn_evaluate(self, key: str, message: str, *args: object) -> None:
        """같은 종류의 evaluate 경고는 `EVALUATE_WARN_INTERVAL_SEC` 에 한 번만 남긴다."""
        now = self._clock()
        next_at, skipped = self._evaluate_warned.get(key, (0.0, 0))
        if now < next_at:
            self._evaluate_warned[key] = (next_at, skipped + 1)
            return
        self._evaluate_warned[key] = (now + EVALUATE_WARN_INTERVAL_SEC, 0)
        suffix = f" (앞서 같은 경고 {skipped}번 생략)" if skipped else ""
        logger.warning(message + suffix, *args)


def _t_ms_in_range(request: dict[str, Any]) -> bool:
    t_ms = request.get("t_ms")
    return isinstance(t_ms, int) and not isinstance(t_ms, bool) and 0 <= t_ms <= MAX_T_MS


def _subject(request: dict[str, Any]) -> str:
    """로그에 남길 대상. evaluate · finalize 는 Take, plan 은 발표다."""
    if "take_id" in request:
        return f"take={request['take_id']}"
    return f"pitch={request.get('pitch_id')}"


def _error_code(response: httpx.Response) -> str:
    """AI 공통 에러 본문의 code. 본문 전체는 남기지 않는다 (사용자 입력이 되돌아올 수 있다)."""
    try:
        body = response.json()
    except ValueError:
        return "-"
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("code", "-"))
    return "-"
