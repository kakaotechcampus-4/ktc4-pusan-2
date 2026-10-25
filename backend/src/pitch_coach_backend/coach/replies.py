"""코치 AI 응답 (`/coach/evaluate` · `/coach/finalize` · `/coach/plan`) 의 모양과 읽는 법.

**받은 그대로 두는 것**: `coach_state` · `events` · `take_result` · `coaching_plan` · `meta`.
`coach_state` 와 `events` 는 다음 요청 · finalize 로 AI 에 다시 가고, 나머지는 받은 그대로 저장하는
게 계약이다(coach-agent.md 12-2). 그래서 BE 가 쓰는 칸만 모양을 검사하고 dict 는 손대지 않는다.
pydantic 모델로 옮겨 담으면 AI 가 더한 필드가 저장 전에 사라진다.

Take 결과에서 `take_summaries` 의 측정 칸을 읽는 것(`summary_measures`)도 여기 둔다. 호출 · 재시도는
`client.py` 다.
"""

import math
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from pitch_coach_backend.core.rounding import round_half_up
from pitch_coach_backend.core.validation import reject_unstorable_json

# 요청 `t_ms` 의 상한. Take 최대 1시간 + 60초 (architecture.md 1-3). 넘으면 AI 가 422 를 준다
MAX_T_MS = 3_660_000


# Take 결과의 영역 중 센서 · STT 로 잰 것. TIME 의 measured_ratio 는 "목표 시간이 있던 시간의
# 비율"이라 측정 품질이 아니다 — BE 는 목표 시간을 늘 싣으므로 늘 1 이고, 평균에 넣으면 다른 영역이
# 못 쟀어도 신뢰도가 높아 보인다
_MEASURED_AREAS = ("GAZE", "SPEED", "VOLUME", "PAUSE", "FILLER")


# take_summaries 칸에 들어가는 범위. 벗어난 값은 잘못 잰 것이다 — 그대로 넣으면 Take 결과 저장이
# 통째로 실패하므로 그 칸만 None 으로 둔다
_VOLUME_LIMIT_DB = 999.99  # volume 은 Numeric(5, 2)

_INTEGER_LIMIT = 2**31 - 1  # Integer 칸


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


class Reply(_View):
    """성공 응답 셋의 공통 칸. `meta` 는 결과와 함께 저장한다 (architecture.md 5절)."""

    meta: Meta


class EvaluateReply(Reply):
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


class FinalizeReply(Reply):
    # 받은 그대로 take_ai_results(TAKE_RESULT).payload 에 둔다
    take_result: Annotated[dict[str, Any], _shaped(_TakeResultView)]
    # 이번에 닫은 이벤트. evaluate 의 이벤트처럼 쌓는다
    events: list[Event] = []

    @property
    def duration_sec(self) -> int:
        """takes.duration_sec. Take 결과의 길이(요청의 t_ms)를 초로 반올림한다."""
        return round_half_up(self.take_result["duration_ms"] / 1000)

    def summary(self) -> SummaryMeasures:
        return summary_measures(self.take_result)


class PlanReply(Reply):
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
        return None if value is None else _fits(round_half_up(value, scale=100))

    def count(area: str, name: str) -> int | None:
        value = metric(area, name)
        return None if value is None else _fits(round_half_up(value))

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
            round_half_up(sum(ratios) / len(ratios), scale=100) if ratios else None
        ),
    )


def _fits(value: int) -> int | None:
    return value if abs(value) <= _INTEGER_LIMIT else None
