"""시선 서버 판정 모듈의 입출력 계약 (pydantic).

기준: docs/ai/features/gaze-tracking.md 5절. 공통 필드(1초 판정 결과 · 커서 · criteria)는
docs/ai/architecture.md 7절을 따른다. 시각은 모두 Take 시작 기준 ms 다.

입력은 1초 기록(브라우저 엔진이 만들어 FE → BE → 코치로 오는 단위)과 같은 1초들의 발화 시간이다.
**얼굴 영상 · 랜드마크 · 얼굴 측정값은 들어 있지 않다.** 입력 모델은 모르는 필드를 무시하고, 출력 모델은
정해진 필드만 낸다(gaze-tracking.md 5절). 다른 모듈을 import 하지 않으므로 architecture.md 7절의 공통
결과 모양(`JudgmentIssue` · `JudgmentResult` · `TallyItem` · `IssueCriteria`)도 여기에 옮겨 둔다.

아래쪽의 옛 출력 모델(`GazeIssue` · `GazeTakeSummary` 등)은 옛 코어(`evaluate_gaze` 등)의 계약이다.
연구 도구가 새 함수로 옮기면 지운다.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

if TYPE_CHECKING:
    from .core import GazeSample

#: 1초 기록의 상태 (엔진 상태). 판정된 4개 + UNCERTAIN(판정 보류) + UNMEASURED(잴 수 없음)
SampleState = Literal["CAMERA", "SCREEN", "BOTTOM", "OTHER", "UNCERTAIN", "UNMEASURED"]
#: OTHER 를 본 방향 (발표자 기준 8방향)
Direction = Literal[
    "RIGHT", "UP_RIGHT", "UP", "UP_LEFT", "LEFT", "DOWN_LEFT", "DOWN", "DOWN_RIGHT"
]  # fmt: skip
_DIRECTIONS = frozenset(get_args(Direction))
#: 1초 기록에서 빠지거나 null 이면 0 으로 읽는 선택 필드
_ZERO_IF_MISSING = frozenset({"confidence", "reliability", "frames"})
IssueType = Literal[
    "GAZE_ON_SCRIPT", "GAZE_ON_SCREEN", "GAZE_AWAY", "GAZE_LOW_EYE_CONTACT", "GAZE_UNMEASURABLE"
]  # fmt: skip
#: 판정 상태 (1초 판정 결과의 state)
JudgedState = Literal["AUDIENCE", "SCREEN", "SCRIPT", "AWAY", "UNMEASURABLE"]

Ratio = float  # 0~1, 소수 4자리 (core.r4)


class _Input(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Output(BaseModel):
    # 출력 계약은 키 집합이 고정이다. 코어가 키를 더하거나 빠뜨리면 계약 테스트가 깨진다
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# 입력: judge(inputs, t_ms, config) 의 inputs (gaze-tracking.md 5-2)
# --------------------------------------------------------------------------


class GazeRecord(_Input):
    """시선 1초 기록 한 개 (architecture.md 6-2). 읽을 수 없으면 그 1초만 버린다(core.read_records)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    t_ms: int = Field(ge=0, description="Take 시작 기준 이 1초의 시작 시각")
    duration_ms: int = Field(gt=0, description="보통 1000. Take 끝의 마지막 조각만 짧다")
    state: SampleState
    direction: Direction | None = Field(default=None, description="state 가 OTHER 일 때만")
    confidence: Ratio = Field(default=0.0, ge=0, le=1, description="이긴 상태에 투표한 프레임 비율")
    reliability: Ratio = Field(default=0.0, ge=0, le=1, description="촬영 조건 신뢰도의 평균")
    issues: tuple[str, ...] = Field(default=(), description="촬영 조건 이슈. 해석하지 않는다")
    frames: int = Field(default=0, ge=0, description="이 1초에 들어온 프레임 수")

    @model_validator(mode="before")
    @classmethod
    def _keep_what_can_be_read(cls, data: Any) -> Any:
        # 뜻을 읽을 수 있는 정보는 버리지 않는다. 방향은 OTHER 에만 뜻이 있고, 모르는 방향(엔진이 방향을
        # 늘린 경우 · 문자열이 아닌 값)은 방향만 버린다. issues 가 null 이면 빈 목록이고, 문자열이 아닌
        # 값은 무시한다. 선택 필드가 null 이면 빠진 것과 같다(없으면 0, gaze-tracking.md 5-2) -- 코치의
        # 요청 모델은 빠진 confidence · reliability · frames 를 null 로 넘긴다
        if not isinstance(data, dict):
            return data
        data = {k: v for k, v in data.items() if not (k in _ZERO_IF_MISSING and v is None)}
        direction = data.get("direction")
        if data.get("state") != "OTHER" or not (
            isinstance(direction, str) and direction in _DIRECTIONS
        ):
            data["direction"] = None
        issues = data.get("issues")
        data["issues"] = (
            [i for i in issues if isinstance(i, str)] if isinstance(issues, list) else []
        )
        return data


class Voiced(_Input):
    """같은 1초의 발화 시간. 코치가 음량 1초 기록에서 골라 넣는다(오디오가 끊긴 1초는 뺀다)."""

    t_ms: int
    voiced_ms: int = Field(ge=0)


class GazeInput(_Input):
    #: 판정 시각(그 요청의 t_ms, 마지막 1초가 끝난 시각). judge 의 t_ms 인자와 같아야 한다
    t_ms: int = Field(ge=0)
    #: 시각 커서. t_ms 가 이 값 이상인 1초가 새것이다
    since_ms: int = Field(ge=0)
    #: 최근 시선 1초 기록. 기록 하나씩 읽어 이상한 것은 그 1초만 버리므로 여기서는 모양을 보지 않는다
    records: list[Any] = Field(default_factory=list)
    #: 같은 1초들의 발화 시간
    voiced: list[Voiced] = Field(default_factory=list)


# --------------------------------------------------------------------------
# 출력: judge → JudgmentResult 하나를 담은 목록 (gaze-tracking.md 5-3, architecture.md 7절)
# --------------------------------------------------------------------------


class GazeMetrics(_Output):
    """판정에 쓴 값. 분모가 0 인 비율은 None(measured_ratio 만 0)."""

    #: 최근 창 (measured_ms 로 나눈다)
    audience_ratio: Ratio | None
    screen_ratio: Ratio | None
    script_ratio: Ratio | None
    away_ratio: Ratio | None
    #: 최근 창에서 말한 시간 중 대본을 본 비율 (대본 의존)
    reading_ratio: Ratio | None
    #: 짧은 창 (개입 효과용)
    audience_ratio_short: Ratio | None
    script_ratio_short: Ratio | None
    away_ratio_short: Ratio | None
    #: 짧은 창의 측정된 시간 ÷ 창 길이
    measured_ratio: Ratio
    #: 짧은 창 1초 기록들의 reliability 평균. 기록이 없으면 0
    mean_reliability: Ratio
    #: 판정 시각까지 지금 이어지는 응시 길이. 이어지지 않으면 0
    script_run_ms: int
    screen_run_ms: int
    away_run_ms: int


class TallyItem(_Output):
    """새로 센 1초 하나(t_ms 는 그 1초의 시작). 값이 0 인 키는 뺀다.

    values ⊂ {camera_ms, screen_ms, script_ms, away_ms, uncertain_ms, unmeasured_ms,
    measured_ms, total_ms, speaking_ms, reading_ms, reading_runs}
    """

    t_ms: int
    values: dict[str, int]


class JudgmentIssue(_Output):
    issue_type: IssueType
    area: Literal["GAZE"] = "GAZE"
    #: 기준값에서 0.5, 아주 나쁨 값에서 1.0, 그 사이 직선, 1.0 에서 자른다
    severity: Ratio = Field(ge=0, le=1)
    #: 측정 비율이 높을수록 크다. 측정 불가 신호는 1.0
    confidence: Ratio = Field(ge=0, le=1)
    persistence_sec: float = Field(ge=0)
    threshold: float
    bad: float
    #: 키는 지표 이름, 짧은 창 값은 _short
    evidence: dict[str, float | int | None] = Field(default_factory=dict)
    #: GAZE_UNMEASURABLE(측정 불가 신호)만 False
    actionable: bool


class JudgmentResult(_Output):
    evaluator: Literal["gaze"] = "gaze"
    area: Literal["GAZE"] = "GAZE"
    t_ms: int
    #: 이번까지 센 시각 = max(t_ms, since_ms). 코치가 다음 요청의 since_ms 로 넘긴다
    counted_until_ms: int
    #: gaze-<버전>+<config 해시 12자리>
    criteria_version: str
    measurable: bool
    #: 잴 수 없으면 UNMEASURABLE
    state: JudgedState
    metrics: GazeMetrics
    tally: list[TallyItem]
    #: 잴 수 없으면 GAZE_UNMEASURABLE 만
    issues: list[JudgmentIssue]


# --------------------------------------------------------------------------
# 출력: criteria(config) (gaze-tracking.md 5-3)
# --------------------------------------------------------------------------


class IssueCriteria(_Output):
    metric: str
    direction: Literal["HIGHER_IS_WORSE", "LOWER_IS_WORSE"]
    threshold: float
    bad: float
    #: 판정 창 때문에 문제의 시작 · 끝이 늦게 잡히는 폭
    onset_lag_ms: int
    offset_lag_ms: int


# --------------------------------------------------------------------------
# 출력: summarize(tally, config) → {"GAZE": {...}} (gaze-tracking.md 5-3 · 14절)
# --------------------------------------------------------------------------

#: summarize 입력: 장 · Take 동안의 tally 합계 (영역 → 이름 → 합)
Tally = dict[str, dict[str, float]]


class GazeSummary(_Output):
    """장 · Take 의 시선 지표. 분모가 0 이면 None(measured_ratio 만 0)."""

    #: camera_ms · screen_ms · script_ms · away_ms ÷ measured_ms
    audience_ratio: Ratio | None
    script_ratio: Ratio | None
    screen_ratio: Ratio | None
    away_ratio: Ratio | None
    #: reading_ms ÷ speaking_ms (대본 의존)
    reading_ratio: Ratio | None
    #: 말한 1초 동안 대본 응시가 기준 시간 이상 이어졌다가 끝난 구간의 수
    reading_runs: int
    #: measured_ms ÷ total_ms (시선의 측정 비율)
    measured_ratio: Ratio


class Summary(_Output):
    GAZE: GazeSummary


# ==========================================================================
# 옛 코어(evaluate_gaze · take_summary · compare_summaries · intervention_outcome)의 계약
# ==========================================================================


class ByState[T](_Output):
    """판정된 4개 상태마다 값 하나 (키가 모두 있어야 한다)."""

    CAMERA: T
    SCREEN: T
    BOTTOM: T
    OTHER: T


class BySampleState(ByState[int]):
    """1초 기록의 6개 상태마다 시간(ms)."""

    UNCERTAIN: int
    UNMEASURED: int


class ByDirection(_Output):
    """OTHER 를 본 8방향마다 시간(ms)."""

    RIGHT: int
    UP_RIGHT: int
    UP: int
    UP_LEFT: int
    LEFT: int
    DOWN_LEFT: int
    DOWN: int
    DOWN_RIGHT: int


class GazeSampleRecord(BaseModel):
    """1초 기록 한 개 (옛 코어 입구)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    t_ms: int = Field(ge=0, description="테이크 시작 기준 이 1초의 시작 시각")
    duration_ms: int = Field(gt=0, description="보통 1000. 테이크 끝의 마지막 조각만 짧다")
    state: SampleState
    direction: Direction | None = Field(default=None, description="state 가 OTHER 일 때만")
    confidence: Ratio = Field(default=0.0, ge=0, le=1, description="이긴 상태에 투표한 프레임 비율")
    reliability: Ratio = Field(default=0.0, ge=0, le=1, description="촬영 조건 신뢰도의 평균")
    issues: list[str] = Field(
        default_factory=list, description="프레임 절반 이상에 나온 촬영 조건 이슈"
    )
    frames: int = Field(default=0, ge=0, description="이 1초에 들어온 프레임 수")

    @model_validator(mode="before")
    @classmethod
    def _keep_what_can_be_read(cls, data: Any) -> Any:
        # 뜻을 읽을 수 있는 정보는 버리지 않는다. 방향은 OTHER 에만 뜻이 있고, 모르는 방향(엔진이 방향을
        # 늘린 경우 등)은 방향만 버린다. issues 가 null 이면 빈 목록이다. 나머지 정보는 그대로 쓴다.
        # 빠진 신뢰도 · 프레임 수는 코어의 GazeSample.from_dict 와 같은 기본값(0)이다
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if data.get("state") != "OTHER" or data.get("direction") not in _DIRECTIONS:
            data["direction"] = None
        if data.get("issues") is None:
            data["issues"] = []
        return data

    def to_sample(self) -> GazeSample:
        # core 가 이 모듈을 import 하므로 여기서는 쓸 때 가져온다(순환 import 를 피한다)
        from .core import GazeSample

        return GazeSample.from_dict(self.model_dump())


def parse_records(raw: Iterable[Any]) -> tuple[list[GazeSample], int]:
    """요청 본문의 1초 기록 목록 → 옛 코어의 GazeSample 목록과 버린 기록 수.

    뜻을 읽을 수 없는 기록(모르는 상태, 범위 밖 값 등)은 그 1초만 버리고 나머지는 쓴다.
    버린 시간은 ``core.normalize_samples`` 가 '측정 못 함'으로 채운다.
    """
    samples: list[GazeSample] = []
    dropped = 0
    for item in raw:
        try:
            samples.append(GazeSampleRecord.model_validate(item).to_sample())
        except ValidationError:
            dropped += 1
    return samples, dropped


class GazeIssue(_Output):
    """``evaluate_gaze`` 의 이슈 하나 (옛 평가기 형식)."""

    evaluator: Literal["gaze"]
    issue_type: IssueType
    t_ms: int
    severity: Ratio = Field(ge=0, le=1)
    confidence: Ratio = Field(ge=0, le=1)
    persistence_sec: float = Field(ge=0)
    #: 이슈마다 키가 다르고 창 길이가 키 이름에 들어간다 (bottom_ratio_5s 등, core.evaluate_gaze)
    evidence: dict[str, Any]
    #: False 면 이 이슈로는 피드백하지 않는다. GAZE_UNMEASURABLE(시선 피드백 보류 신호)만 False
    actionable: bool


class Intervention(_Output):
    type: str
    issue_type: IssueType
    t_ms: int


class GazeInterventionOutcome(BaseModel):
    """``intervention_outcome`` — 피드백 전후 대상 비율. 뒤 창의 키는 ``after_<지연초>s`` 다."""

    model_config = ConfigDict(extra="allow")

    intervention: Intervention
    before: dict[str, float | int | None]
    #: 어느 창이든 측정된 시간이 없으면 None (아직 판단 불가)
    effective: bool | None

    @model_validator(mode="after")
    def _one_after_window(self) -> GazeInterventionOutcome:
        extra = self.model_extra or {}
        if len(extra) != 1 or not next(iter(extra)).startswith("after_"):
            raise ValueError(f"expected exactly one after_<delay>s window, got {sorted(extra)}")
        return self


class GazeSegment(_Output):
    """같은 상태가 이어진 구간 (OTHER 는 방향이 달라도 한 구간)."""

    state: SampleState
    start_ms: int
    end_ms: int
    duration_ms: int
    direction: Direction | None
    mean_confidence: Ratio
    mean_reliability: Ratio


class GazeProblemSegment(GazeSegment):
    """이슈 조건(대본 3초 · 화면 5초 · 다른 곳 2초)을 넘긴 구간."""

    issue_type: IssueType


class GazeTakeSummary(_Output):
    """``take_summary`` — 테이크 전체 통계 · 구간 · 문제 구간.

    기록이 하나도 없으면 ``evaluator`` · ``tracked_ms`` · ``measured_ms`` · 빈 구간 목록만 온다.
    """

    evaluator: Literal["gaze"]
    tracked_ms: int
    measured_ms: int
    segments: list[GazeSegment]
    problem_segments: list[GazeProblemSegment]
    start_ms: int | None = None
    end_ms: int | None = None
    uncertain_ms: int | None = None
    unmeasured_ms: int | None = None
    #: 측정된 시간 / 기록된 시간
    coverage: Ratio | None = None
    state_ms: BySampleState | None = None
    #: 측정된 시간이 0 이면 각 비율이 None
    state_ratio: ByState[Ratio | None] | None = None
    #: = state_ratio["CAMERA"]
    eye_contact_ratio: Ratio | None = None
    other_direction_ms: ByDirection | None = None
    mean_reliability: Ratio | None = None
    condition_issue_ms: dict[str, int] | None = None
    longest_run_ms: ByState[int] | None = None
    episodes: ByState[int] | None = None


class Delta(_Output):
    previous: float | None
    current: float | None
    #: 한쪽이라도 없으면 None
    delta: float | None


class GazeSummaryComparison(_Output):
    """``compare_summaries`` — 이전 테이크와 나란히 (delta = current − previous)."""

    eye_contact_ratio: Delta
    coverage: Delta
    mean_reliability: Delta
    state_ratio: ByState[Delta]
    episodes: ByState[Delta]
    longest_run_ms: ByState[Delta]
