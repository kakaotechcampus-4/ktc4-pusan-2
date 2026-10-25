"""AI 실시간 코치 호출. DB 를 모른다 (module/pitch/adapter/script_parser.py 와 같은 자리).

    BE ──POST /coach/plan      (백그라운드, LLM)       ──▶ AI
    BE ──POST /coach/evaluate  (발표 중 metrics 마다)  ──▶ AI
    BE ──POST /coach/finalize  (Take 종료 뒤 백그라운드) ──▶ AI

계약은 `docs/ai/features/coach-agent.md` 5 · 8절, 공통 규칙은 `docs/ai/architecture.md` 2~5절이다.
AI 응답을 BE 형식으로 바꾸는 곳은 이 파일 하나다(architecture.md 2절). AI 응답 모양이 바뀌면
여기만 고친다.

**받은 그대로 두는 것**: `coach_state` · `events` · `take_result` · `coaching_plan` · `meta`.
`coach_state` 와 `events` 는 다음 요청 · finalize 로 AI 에 다시 가고, 나머지는 받은 그대로 저장하는
게 계약이다(12-2). 그래서 BE 가 쓰는 칸만 모양을 검사하고 dict 는 손대지 않는다. pydantic 모델로
옮겨 담으면 AI 가 더한 필드가 저장 전에 사라진다.

호출 규칙 (coach-agent.md 8절)
  - evaluate: 300ms, 다시 부르지 않는다. 늦은 응답은 버린다 — 다음 1초 요청이 대신한다
  - finalize: 10초, `replay` 를 붙인 호출만 60초. 409 `REPLAY_REQUIRED` 는 `ReplayRequired` 로
    돌려준다. `replay` 를 만들어 다시 부르는 건 호출자 몫이다(원자료가 DB 에 있다)
  - plan: 30초. 200 + `fallback_reason` 도 정상 결과다
  - plan · finalize 는 일시 오류(연결 실패 · 시간 초과 · 408 · 429 · 5xx)면 한 번 다시 부른다.
    그 밖의 4xx 는 다시 불러도 같다
  - plan · finalize 는 프로세스 전체에서 동시에 `BACKGROUND_CONCURRENCY` 개까지 (architecture.md
    3절). 긴 요청이 AI 스레드 풀을 채우면 1초마다 오는 evaluate 가 300ms 를 넘긴다
  - 요청마다 `X-Request-ID`. 다시 부를 때도 같은 값이다 — AI 로그에서 두 시도가 한 요청으로 묶인다
  - 로그에는 상태 코드와 `error.code` 만 남긴다. 본문에는 대본 · 전사가 되돌아올 수 있다

실패는 던지지 않고 None 을 돌려준다. 코칭이 안 된다고 발표가 멈추면 안 되고, 호출자가 할 수 있는
일도 "이번 1초는 건너뛴다" · "Take 를 FAILED 로 둔다" 정도다.
"""

import asyncio
import json
import logging
import math
import ssl
import time
import uuid
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from typing import Annotated, Any, Protocol

import httpx
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from pitch_coach_backend.core.validation import reject_unstorable_json

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
BACKGROUND_CONCURRENCY = 4

# AI 가 없을 때(연결 못 함 · 경로 없음 404 · 준비 중 503) evaluate 를 쉬는 시간. 프로세스 전체에
# 건다. 쉬지 않으면 Take 마다 1초에 한 번씩 죽은 서버를 두드린다. 시선 · 음량 창(30초)보다 짧아야
# AI 가 돌아왔을 때 빠진 1초 없이 이어진다 — 쉬는 동안의 1초 기록은 다음 요청의 창에 실린다
EVALUATE_PAUSE_SEC = 10.0
# 같은 evaluate 실패 경고를 다시 남기는 간격. 매초 오는 요청이라 그대로 두면 로그가 덮인다
EVALUATE_WARN_INTERVAL_SEC = 60.0

# 요청 `t_ms` 의 상한. Take 최대 1시간 + 60초 (architecture.md 1-3). 넘으면 AI 가 422 를 준다
MAX_T_MS = 3_660_000

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

# Take 결과의 영역 중 센서 · STT 로 잰 것. TIME 의 measured_ratio 는 "목표 시간이 있던 시간의
# 비율"이라 측정 품질이 아니다 — BE 는 목표 시간을 늘 싣으므로 늘 1 이고, 평균에 넣으면 다른 영역이
# 못 쟀어도 신뢰도가 높아 보인다
_MEASURED_AREAS = ("GAZE", "SPEED", "VOLUME", "PAUSE", "FILLER")
# take_summaries 칸에 들어가는 범위. 벗어난 값은 잘못 잰 것이다 — 그대로 넣으면 Take 결과 저장이
# 통째로 실패하므로 그 칸만 None 으로 둔다
_VOLUME_LIMIT_DB = 999.99  # volume 은 Numeric(5, 2)
_INTEGER_LIMIT = 2**31 - 1  # Integer 칸


@cache
def _ssl_context() -> ssl.SSLContext:
    # 클라이언트마다 CA 번들을 다시 읽지 않게 한 번만 만든다 (script_parser.py 와 같은 이유)
    return httpx.create_ssl_context()


# ── AI 응답 ───────────────────────────────────────────────────────────
# 모르는 필드는 무시한다(architecture.md 2절). `_View` 는 검사만 하는 모양이고, 저장 · 재전송할
# 값은 `_shaped` 로 검사한 뒤 받은 dict 그대로 둔다


class _View(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _shaped(view: type[BaseModel]) -> AfterValidator:
    """dict 가 view 모양인지 검사만 하고 받은 그대로 돌려준다. 그대로 JSONB 에 넣으므로 넣을 수
    없는 값(NUL 문자 · NaN · Infinity)도 거절한다 — 넣다 실패하면 뒤 저장까지 막힌다."""

    def check(value: dict[str, Any]) -> dict[str, Any]:
        view.model_validate(value)
        reject_unstorable_json(value)
        return value

    return AfterValidator(check)


class _MetaView(_View):
    schema_version: str
    feature_version: str


class _EventView(_View):
    # 저장 칸(coach_events)이 이 셋을 쓴다. 나머지는 kind 마다 다르고 BE 는 읽지 않는다
    event_id: str
    kind: str
    # Integer 칸이다. 넘는 값을 넣으면 그 저장이 끝내 실패해 뒤 이벤트까지 막힌다
    t_ms: int = Field(ge=0, le=_INTEGER_LIMIT)


class _AreaView(_View):
    measured_ratio: float | None = None
    # 그 영역 모듈의 summarize 결과. 측정 비율이 기준 미만이면 null
    take: dict[str, Any] | None = None


class _TakeResultView(_View):
    take_id: str
    duration_ms: int = Field(strict=True, ge=0, le=MAX_T_MS)
    areas: dict[str, _AreaView]


class _CoachingPlanView(_View):
    focus: list[dict[str, Any]] = []
    relax: list[dict[str, Any]] = []
    max_interventions: int | None = None


Meta = Annotated[dict[str, Any], _shaped(_MetaView)]
Event = Annotated[dict[str, Any], _shaped(_EventView)]


class Feedback(_View):
    """`INTERVENE` 일 때 화면에 띄울 한마디. `area` 는 원인 영역, `instruction` 은 할 행동이다."""

    area: str
    instruction: str
    # 템플릿 문장이다. LLM 이 만들지 않는다
    message: str
    priority: int
    confidence: float
    # 문제 구간 start_ms · end_ms · slide_number 와 판정 모듈이 준 근거 값. 받은 그대로 저장한다
    evidence: dict[str, Any] = {}


class _Reply(_View):
    """성공 응답 셋의 공통 칸. `meta` 는 결과와 함께 저장한다 (architecture.md 5절)."""

    meta: Meta


class EvaluateReply(_Reply):
    # WAIT · IGNORE · INTERVENE. 한 스키마 버전 안에서 값이 늘 수 있어 문자열로 받는다
    action: str
    feedback: Feedback | None = None
    # 영역 → 상태. 빈 dict 는 '바뀌지 않음'이다 (STALE_TICK · T_MS_JUMP · INTERNAL_ERROR)
    indicators: dict[str, str] = {}
    reason_codes: list[str] = []
    events: list[Event] = []
    # 다음 요청에 그대로 붙인다. BE 는 내용을 읽지 않는다
    coach_state: dict[str, Any]

    @property
    def intervened(self) -> bool:
        return self.action == "INTERVENE" and self.feedback is not None


class FinalizeReply(_Reply):
    # 받은 그대로 take_ai_results(TAKE_RESULT).payload 에 둔다
    take_result: Annotated[dict[str, Any], _shaped(_TakeResultView)]
    # 이번에 닫은 이벤트. evaluate 의 이벤트처럼 쌓는다
    events: list[Event] = []

    @property
    def duration_sec(self) -> int:
        """takes.duration_sec. Take 결과의 길이(요청의 t_ms)를 초로 반올림한다."""
        return _round_half_up(self.take_result["duration_ms"] / 1000)

    def summary(self) -> SummaryMeasures:
        return summary_measures(self.take_result)


class PlanReply(_Reply):
    # focus · relax · max_interventions. 다음 Take 의 evaluate 에 실을 때 why 만 뺀다
    coaching_plan: Annotated[dict[str, Any], _shaped(_CoachingPlanView)]
    # 요청 값 그대로 돌아온다 (오래된 계획 막기용, coach-agent.md 12-2)
    source_take_id: str | None = None
    script_version_id: str | None = None
    # LLM 대신 기본 계획을 냈으면 그 이유. 이 응답도 정상 결과로 저장한다
    fallback_reason: str | None = None
    dropped: list[str] = []
    planner_hash: str


@dataclass(frozen=True)
class ReplayRequired:
    """finalize 가 409 `REPLAY_REQUIRED` 를 줬다. 코치가 Take 를 다 세지 못했다 — 원자료(`replay`)를
    붙여 한 번 더 불러야 한다. 빠진 구간은 에러 `message` 에만 있고 BE 는 쓰지 않는다(Take 전체를
    보낸다)."""


@dataclass(frozen=True)
class SummaryMeasures:
    """`take_summaries` 의 측정 칸 (architecture.md 1-1 대응 표). 잴 수 없던 값은 None."""

    # 말 속도 CPM(분당 글자 수)
    average_cpm: float | None
    filler_count: int | None
    # 비율 × 100 반올림 (0~100)
    audience_gaze: int | None
    slide_gaze: int | None
    script_gaze: int | None
    script_dependency_count: int | None
    # 평소 목소리 대비 dB
    volume: float | None
    total_intermission: int | None
    # 다섯 영역 측정 비율 평균 × 100 반올림
    overall_confidence: int | None


def summary_measures(take_result: dict[str, Any]) -> SummaryMeasures:
    """Take 결과에서 리포트 목록 · 비교에 쓰는 값만 꺼낸다. 영역 값이 없거나(측정 비율 미달) 지표가
    null(분모 0)이면 그 칸은 None 이다."""
    areas = _TakeResultView.model_validate(take_result).areas

    def metric(area: str, name: str) -> float | None:
        result = areas.get(area)
        if result is None or result.take is None:
            return None
        value = result.take.get(name)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        if not math.isfinite(value):
            return None
        return float(value)

    def percent(area: str, name: str) -> int | None:
        value = metric(area, name)
        return None if value is None else _fits(_round_half_up(value, scale=100))

    def count(area: str, name: str) -> int | None:
        value = metric(area, name)
        return None if value is None else _fits(_round_half_up(value))

    volume = metric("VOLUME", "voice_diff_db")

    ratios = [
        result.measured_ratio
        for area in _MEASURED_AREAS
        if (result := areas.get(area)) is not None and result.measured_ratio is not None
    ]
    return SummaryMeasures(
        average_cpm=metric("SPEED", "cpm"),
        filler_count=count("FILLER", "filler_count"),
        audience_gaze=percent("GAZE", "audience_ratio"),
        slide_gaze=percent("GAZE", "screen_ratio"),
        script_gaze=percent("GAZE", "script_ratio"),
        script_dependency_count=count("GAZE", "reading_runs"),
        volume=volume if volume is not None and abs(volume) <= _VOLUME_LIMIT_DB else None,
        total_intermission=count("SPEED", "pause_count"),
        # 비율 × 100 칸은 .5 를 올려 정수로 넣는다 (architecture.md 1-1 '반올림')
        overall_confidence=(
            _round_half_up(sum(ratios) / len(ratios), scale=100) if ratios else None
        ),
    )


def _fits(value: int) -> int | None:
    return value if abs(value) <= _INTEGER_LIMIT else None


def _round_half_up(value: float, *, scale: int = 1) -> int:
    """value × scale 을 반올림한다(.5 는 올림).

    round() 는 짝수 쪽으로 반올림하고(round(0.5) == 0), floor(x + 0.5) 는 이진 소수 오차로
    0.285 × 100 = 28.499… 를 28 로 만든다. 그래서 값을 적힌 자릿수(repr) 그대로 Decimal 로 센다.
    """
    exact = Decimal(repr(value)) * scale
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))


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
    # 동시 개수 제한을 받는 백그라운드 호출인가
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
    금방이고, 앱 수명에 클라이언트를 묶지 않아도 된다. 동시 개수 제한 · evaluate 쉬기는 이 객체가
    들고 있으므로 프로세스에 하나만 만든다 (`coach/dependencies.py`).
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
        background_concurrency: int = BACKGROUND_CONCURRENCY,
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
        self._background_concurrency = background_concurrency
        # 이벤트 루프마다 하나. asyncio.Semaphore 는 처음 기다린 루프에 묶여서, 하나를 여러 루프가
        # 쓰면(테스트의 TestClient 마다 루프가 다르다) 다른 루프에서 기다리는 순간 RuntimeError 다.
        # 운영은 uvicorn 루프 하나라 프로세스 전체에 하나다
        self._background: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Semaphore
        ] = weakref.WeakKeyDictionary()
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

    async def _call[T: _Reply](
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
                    async with self._slots():
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

    async def _attempt[T: _Reply](
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

    def _slots(self) -> asyncio.Semaphore:
        """지금 이벤트 루프의 백그라운드 호출 자리."""
        loop = asyncio.get_running_loop()
        slots = self._background.get(loop)
        if slots is None:
            slots = asyncio.Semaphore(self._background_concurrency)
            self._background[loop] = slots
        return slots

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
