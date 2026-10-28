"""시선 서버 판정 모듈의 입출력 계약 (pydantic).

기준: docs/ai/features/gaze-tracking.md 5절. 공통 필드(1초 판정 결과 · 커서 · criteria)는
docs/ai/architecture.md 7절을 따른다. 시각은 모두 Take 시작 기준 ms 다.

입력은 1초 기록(브라우저 엔진이 만들어 FE → BE → 코치로 오는 단위)과 같은 1초들의 발화 시간이다.
**얼굴 영상 · 랜드마크 · 얼굴 측정값은 들어 있지 않다.** 입력 모델은 모르는 필드를 무시하고,
출력 모델은 정해진 필드만 낸다(gaze-tracking.md 5절). 다른 모듈을 import 하지 않으므로
architecture.md 7절의 공통 결과 모양(`JudgmentIssue` · `JudgmentResult` · `TallyItem` ·
`IssueCriteria`)도 여기에 옮겨 둔다.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    """시선 1초 기록 한 개 (architecture.md 6-2).
    읽을 수 없으면 그 1초만 버린다(core.read_records)."""

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
        # 뜻을 읽을 수 있는 정보는 버리지 않는다. 방향은 OTHER 에만 뜻이 있고, 모르는 방향
        # (엔진이 방향을 늘린 경우 · 문자열이 아닌 값)은 방향만 버린다. issues 가 null 이면
        # 빈 목록이고, 문자열이 아닌 값은 무시한다. 선택 필드가 null 이면 빠진 것과 같다
        # (없으면 0, gaze-tracking.md 5-2) -- 코치의 요청 모델은 빠진 confidence · reliability ·
        # frames 를 null 로 넘긴다
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
    #: 최근 시선 1초 기록.
    #: 기록 하나씩 읽어 이상한 것은 그 1초만 버리므로 여기서는 모양을 보지 않는다
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
