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


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ══════════════════════════════════════════════════════════════════════════
# 요청 — POST /takes/review (명세 3-1)
# ══════════════════════════════════════════════════════════════════════════


class SlideValues(_In):
    """Take 결과의 장별 값. 측정 비율 미달이면 metrics 가 null"""

    slide_number: int
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
    """코치 Take 결과 (명세 2-3). 리뷰가 읽는 필드만"""

    take_id: str
    areas: dict[str, AreaResult]
    problem_segments: list[ProblemSegment] = Field(default_factory=list)
    gave_up: list[GaveUp] = Field(default_factory=list)


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
    #: 리뷰가 낸 반복 문제에만 있다. 없으면(예전 리뷰) area 로 차원을 찾는다
    dimension: str | None = None


class CriteriaTarget(_In):
    """사용자 기준 하나. 숫자 목표(metric · operator · value)가 없으면 판정하지 않는다"""

    display: str | None = None
    metric: str | None = None
    operator: Operator | None = None
    value: Number | None = None
    slide_number: int | None = None


class UserCriteria(_In):
    display_criteria: list[str] = Field(default_factory=list)
    targets: list[CriteriaTarget] = Field(default_factory=list)
    #: true 면 대본 응시(GAZE_ON_SCRIPT)를 문제로 보지 않는다
    script_used: bool | None = None


class ReviewRequest(_In):
    take_result: TakeResult
    #: 모든 장이 실패했으면 null
    script_coverage: ScriptCoverage | None = None
    #: 같은 발표의 이전 Take (최근 3개). 1차는 이전 Take 비교를 하지 않아 받기만 한다
    previous_takes: list[Any] | None = None
    previous_recurring_issues: list[RecurringIssueIn] | None = None
    missions: list[Mission] | None = None
    user_criteria: UserCriteria | None = None


# ══════════════════════════════════════════════════════════════════════════
# 응답 (명세 3-3)
# ══════════════════════════════════════════════════════════════════════════


class Strength(_Out):
    area: str
    text: str
    evidence: dict[str, Number]


class Improvement(_Out):
    area: str
    slide_number: int | None
    text: str
    evidence: dict[str, Number]


class Comparison(_Out):
    metric: str
    slide_number: int | None = None
    previous: Number
    current: Number
    improved: bool


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
    description: str | None
    target: MissionTarget
    priority: int
    reason: str


class RecurringIssue(_Out):
    area: str
    slide_number: int | None
    #: 명세에 없는 필드. 다음 리뷰가 area 로 차원을 거꾸로 찾지 않게 (GAZE 는 두 차원)
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
    #: 1차: 이전 Take 비교를 하지 않아 늘 빈 목록
    comparison: list[Comparison]
    mission_results: list[MissionResult]
    criteria_results: list[CriteriaResult]
    next_missions: list[NextMission]
    recurring_issues: list[RecurringIssue]
    fallback_reason: FallbackReason | None
    meta: Meta
