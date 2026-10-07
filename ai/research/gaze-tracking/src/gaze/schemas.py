"""시선 서버 코어의 입출력 계약 (pydantic).

입력은 1초 기록(``GazeSampleRecord``)이다. 브라우저 엔진이 만들어(evidence.ts ``sampleToDict``)
FE → BE → AI 서버로 오는 단위이고, **얼굴 영상 · 랜드마크 · 얼굴 측정값은 들어 있지 않다.**
필드 8개가 전부다. 모르는 필드는 버린다(받는 쪽은 모르는 필드를 무시한다).

출력은 core 함수가 돌려주는 dict 의 모양이다. core 는 dict 를 그대로 돌려주고, 이 모델들은
API 응답 · 계약 문서 · 계약 테스트(tests/unit/test_schemas.py)에 쓴다. 이슈의 ``evidence`` 와
개입 효과의 창 키에는 창 길이가 이름에 들어간다(``bottom_ratio_5s``, ``after_5s``) — 창 길이를
바꾸면 키 이름도 바뀌어서, 에이전트가 어느 창의 값인지 헷갈리지 않게 하기 위해서다.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .core import GazeSample

#: 1초 기록의 상태. 판정된 4개 + UNCERTAIN(얼굴은 있으나 판정 보류) + UNMEASURED(얼굴 없음)
SampleState = Literal["CAMERA", "SCREEN", "BOTTOM", "OTHER", "UNCERTAIN", "UNMEASURED"]
#: OTHER 를 본 방향 (발표자 기준 8방향)
Direction = Literal[
    "RIGHT", "UP_RIGHT", "UP", "UP_LEFT", "LEFT", "DOWN_LEFT", "DOWN", "DOWN_RIGHT"
]  # fmt: skip
_DIRECTIONS = frozenset(get_args(Direction))
IssueType = Literal[
    "GAZE_ON_SCRIPT", "GAZE_ON_SCREEN", "GAZE_AWAY", "GAZE_LOW_EYE_CONTACT", "GAZE_UNMEASURABLE"
]  # fmt: skip

Ratio = float  # 0~1, 소수 4자리 (core.r4)


class _Output(BaseModel):
    # 출력 계약은 키 집합이 고정이다. 코어가 키를 더하거나 빠뜨리면 계약 테스트가 깨진다
    model_config = ConfigDict(extra="forbid")


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


# --------------------------------------------------------------------------
# 입력
# --------------------------------------------------------------------------


class GazeSampleRecord(BaseModel):
    """1초 기록 한 개."""

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
        return GazeSample.from_dict(self.model_dump())


# --------------------------------------------------------------------------
# 출력: 코치
# --------------------------------------------------------------------------


class GazeIssue(_Output):
    """``evaluate_gaze`` 의 이슈 하나 (에이전트 공통 평가기 형식)."""

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


# --------------------------------------------------------------------------
# 출력: 리뷰
# --------------------------------------------------------------------------


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
