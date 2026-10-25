"""BE ↔ 리뷰 계약 (pydantic). 요청 · 응답 모양의 단일 진실 원천입니다.

입력 모델은 모르는 필드를 무시합니다(extra="ignore") — BE 가 필드를 먼저 추가해도 깨지지 않게.
리뷰가 읽는 필드만 적었습니다 (Take 결과 · 대본 전달도의 나머지 필드는 받아도 버린다).
출력 모델은 정해진 필드만 냅니다(extra="forbid"). 필드 이름은 snake_case 입니다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .vocab import DimensionState, FallbackReason, JudgeStatus, Operator

Number = int | float

# 개수 · 길이 한도 (명세 5-2 · 5-3). BE · 코치와 맞춘 계약 값이라 config.py 가 아니라 여기에 둔다.
# 입력이 넘으면 ValidationError (라우터가 422 VALIDATION_ERROR 로 바꾼다)
MAX_PREVIOUS_TAKES = 3
MAX_MISSIONS = 20
MAX_RECURRING_ISSUES = 20
MAX_CRITERIA_TARGETS = 20
#: 다음 미션 개수 · 설명 길이 (코칭 계획 입력 한도, coach-agent.md 5-2)
MAX_NEXT_MISSIONS = 20
MAX_DESCRIPTION_CHARS = 500


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ══════════════════════════════════════════════════════════════════════════
# 요청 — POST /takes/review (명세 5-2)
# ══════════════════════════════════════════════════════════════════════════


class SlideValues(_In):
    """Take 결과의 장별 값. 측정 비율 미달이면 metrics 가 null"""

    slide_number: int
    #: 장 목표 시간. 이전 Take 비교에서 slide_duration_ms 의 기준
    target_ms: int | None = None
    measured_ratio: float | None = None
    metrics: dict[str, Any] | None = None


class AreaResult(_In):
    """Take 결과의 영역 하나. 코치가 측정 비율 미달로 본 영역은
    unmeasured_reason 이 LOW_MEASURED_RATIO 이고 take · slides 가 null"""

    measured_ratio: float | None = None
    unmeasured_reason: str | None = None
    take: dict[str, Any] | None = None
    slides: list[SlideValues] | None = None


class ProblemSegment(_In):
    area: str
    issue_type: str
    #: Take 전체 문제면 null
    slide_number: int | None = None
    #: 코치가 판정 창만큼 되돌린 구간
    start_ms: int
    end_ms: int
    mean_severity: float
    reliable: bool = False


class GaveUp(_In):
    issue_type: str
    slide_number: int | None = None


class TakeResult(_In):
    """코치 Take 결과 (coach-agent.md 5-3). 리뷰가 읽는 필드만"""

    take_id: str
    #: 대본 보기 (HIGHLIGHT · KEYWORD · OFF)
    script_mode: str | None = None
    #: 시간 계획 {target_ms, min_ms, max_ms}. 이전 Take 비교에서 duration_ms 의 기준
    plan: dict[str, Any] | None = None
    areas: dict[str, AreaResult]
    problem_segments: list[ProblemSegment] = Field(default_factory=list)
    gave_up: list[GaveUp] = Field(default_factory=list)
    #: 영역 → {criteria_version, issues}. 리뷰는 criteria_version 만 meta 에 옮겨 싣는다
    criteria: dict[str, Any] = Field(default_factory=dict)


class KeyPoint(_In):
    #: critical · high · normal
    importance: str | None = None
    #: covered · partial · missing · contradicted
    status: str | None = None


class SlideEvaluation(_In):
    slide_number: int
    scores: dict[str, Any] = Field(default_factory=dict)
    key_points: list[KeyPoint] = Field(default_factory=list)


class ScriptCoverage(_In):
    """대본 전달도 채점 (/takes/analyze · /takes/rescore 출력). 리뷰가 읽는 필드만"""

    #: content_coverage · critical_fact_accuracy · script_fidelity · scored_ratio
    take_scores: dict[str, Any] = Field(default_factory=dict)
    evaluations: list[SlideEvaluation] = Field(default_factory=list)
    #: 채점에 실패한 장. 장 번호 또는 {slide_number, ...}
    failed: list[Any] = Field(default_factory=list)


class Target(_In):
    metric: str
    operator: Operator
    value: Number


class Mission(_In):
    mission_id: str
    area: str | None = None
    #: null 이면 Take 전체
    slide_number: int | None = None
    target: Target


class RecurringIssueIn(_In):
    area: str | None = None
    slide_number: int | None = None
    #: 없거나 차원 이름이 아니면 반복 판정에 쓰지 않고 로그에 남긴다 (명세 5-2)
    dimension: str | None = None


class CriteriaTarget(_In):
    """사용자 기준 하나. 숫자 목표(metric · operator · value)가 없으면 판정하지 않는다"""

    display: str | None = None
    metric: str | None = None
    operator: Operator | None = None
    value: Number | None = None


class UserCriteria(_In):
    display_criteria: list[str] = Field(default_factory=list)
    targets: list[CriteriaTarget] = Field(default_factory=list, max_length=MAX_CRITERIA_TARGETS)
    #: true 면 대본 응시(GAZE_ON_SCRIPT)를 문제로 보지 않는다
    script_used: bool | None = None


class PreviousTake(_In):
    take_result: TakeResult
    #: 그 Take 의 대본 전달도 Take 값. 없으면 null
    take_scores: dict[str, Any] | None = None


class ReviewRequest(_In):
    take_result: TakeResult
    #: 모든 장이 실패했으면 null
    script_coverage: ScriptCoverage | None = None
    #: 같은 발표의 이전 Take (번호가 큰 것부터 3개). 비교는 [0](바로 앞 Take)만 쓴다
    previous_takes: list[PreviousTake] | None = Field(default=None, max_length=MAX_PREVIOUS_TAKES)
    previous_recurring_issues: list[RecurringIssueIn] | None = Field(
        default=None, max_length=MAX_RECURRING_ISSUES
    )
    missions: list[Mission] | None = Field(default=None, max_length=MAX_MISSIONS)
    user_criteria: UserCriteria | None = None


# ══════════════════════════════════════════════════════════════════════════
# 응답 (명세 5-3)
# ══════════════════════════════════════════════════════════════════════════


class Strength(_Out):
    area: str
    #: 장이 없으면 null
    slide_number: int | None
    text: str
    evidence: dict[str, Number]


class Improvement(_Out):
    area: str
    slide_number: int | None
    text: str
    evidence: dict[str, Number]


class ScriptMode(_Out):
    #: 대본 보기를 남기지 않은 Take 는 null
    previous: str | None
    current: str | None


class Comparison(_Out):
    metric: str
    slide_number: int | None = None
    previous: Number
    current: Number
    #: 좋아진 방향인지. 방향을 정할 수 없으면(기준 없음) null
    improved: bool | None
    #: 두 Take 의 대본 보기가 다를 때만 {previous, current}, 같으면 null
    script_mode: ScriptMode | None = None


class MissionResult(_Out):
    mission_id: str
    status: JudgeStatus
    metric: str
    operator: Operator
    slide_number: int | None
    target: Number
    observed: Number | None


class CriteriaResult(_Out):
    display: str | None
    metric: str
    operator: Operator
    target: Number
    observed: Number | None
    status: JudgeStatus


class MissionTarget(_Out):
    metric: str
    operator: Operator
    value: Number


class NextMission(_Out):
    """mission_id 는 없다. BE 가 missions 에 저장하며 붙인다"""

    area: str | None
    slide_number: int | None
    description: str | None = Field(max_length=MAX_DESCRIPTION_CHARS)
    target: MissionTarget
    priority: int
    reason: str


class RecurringIssue(_Out):
    area: str
    slide_number: int | None
    #: 다음 리뷰가 반복된 차원을 찾는 값. area 만으로는 차원을 가르지 못한다 (GAZE 는 두 차원)
    dimension: str


class Meta(_Out):
    schema_version: str
    feature_version: str
    criteria_versions: dict[str, str]
    model: str | None


class ReviewResponse(_Out):
    take_id: str
    overall_summary: str
    #: 0–100. 평가할 수 있는 차원이 없으면 null
    score: int | None
    dimensions: dict[str, DimensionState]
    strengths: list[Strength]
    improvements: list[Improvement]
    #: 바로 앞 Take 와 같은 지표 비교. 이전 Take 가 없으면 빈 목록
    comparison: list[Comparison]
    mission_results: list[MissionResult]
    criteria_results: list[CriteriaResult]
    next_missions: list[NextMission] = Field(max_length=MAX_NEXT_MISSIONS)
    recurring_issues: list[RecurringIssue] = Field(max_length=MAX_RECURRING_ISSUES)
    fallback_reason: FallbackReason | None
    meta: Meta
