"""고정 어휘 — 영역 · 차원 · 상태 · 연산자와 차원 표 (명세 3-3).

차원 표(어느 영역 · 지표 · 문제가 어느 차원인가)는 명세가 정한 계약이라 여기에 둔다.
판단 기준값(다음 미션 지표 포함)은 config.py 에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Area = Literal["GAZE", "SPEED", "VOLUME", "PAUSE", "FILLER", "CONTENT", "TIME"]
Dimension = Literal[
    "audience_gaze",
    "script_independence",
    "pacing_rhythm",
    "vocal_delivery",
    "fluency",
    "content_delivery",
]
DimensionState = Literal["IMPROVED", "PRIORITY", "STABLE", "STRENGTH", "NOT_EVALUABLE"]
#: 미션 · 사용자 기준 판정 상태
JudgeStatus = Literal["ACHIEVED", "PARTIAL", "FAILED", "NOT_EVALUABLE"]
Operator = Literal["LT", "LTE", "GT", "GTE", "EQ"]
FallbackReason = Literal["LLM_TIMEOUT", "LLM_ERROR", "LLM_INVALID_OUTPUT", "LLM_UNAVAILABLE"]


@dataclass(frozen=True)
class DimensionSpec:
    areas: tuple[str, ...]
    metrics: tuple[str, ...]
    #: 부담에 넣는 문제. content_delivery 는 문제 구간이 아니라 대본 전달도의 핵심 내용으로 센다
    issues: tuple[str, ...]


#: 명세 3-3 dimensions 표. 순서가 출력 dimensions 의 순서다
DIMENSIONS: dict[str, DimensionSpec] = {
    "audience_gaze": DimensionSpec(
        areas=("GAZE",),
        metrics=("audience_ratio",),
        issues=("GAZE_LOW_EYE_CONTACT", "GAZE_AWAY"),
    ),
    "script_independence": DimensionSpec(
        areas=("GAZE",),
        metrics=("script_ratio", "reading_ratio", "reading_runs"),
        issues=("GAZE_ON_SCRIPT",),
    ),
    "pacing_rhythm": DimensionSpec(
        areas=("SPEED", "PAUSE", "TIME"),
        metrics=("cpm", "pause_count", "long_silence_count", "duration_ms", "slide_duration_ms"),
        issues=(
            "PACE_FAST",
            "PACE_SLOW",
            "LONG_SILENCE",
            "BEHIND_SCHEDULE",
            "AHEAD_OF_SCHEDULE",
            "SLIDE_OVER",
            "TIME_OVER",
        ),
    ),
    "vocal_delivery": DimensionSpec(
        areas=("VOLUME",),
        metrics=("voice_diff_db", "low_ratio"),
        issues=("VOLUME_LOW",),
    ),
    "fluency": DimensionSpec(
        areas=("FILLER",),
        metrics=("filler_per_min", "filler_count"),
        issues=("FILLER_FREQUENT",),
    ),
    "content_delivery": DimensionSpec(
        areas=("CONTENT",),
        metrics=("content_coverage", "critical_fact_accuracy"),
        issues=(),
    ),
}

#: 차원 표에 적힌 순서. 부담이 같을 때 순서를 정한다 (명세 6-1 5 · 7번)
DIMENSION_ORDER: dict[str, int] = {dim: i for i, dim in enumerate(DIMENSIONS)}

#: 부담에 넣지 않는 문제 (명세 3-3)
NON_BURDEN_ISSUES = frozenset({"GAZE_ON_SCREEN", "GAZE_UNMEASURABLE", "FINAL_MINUTE"})

#: key_points.status 중 부담으로 세는 것. partial 은 세지 않는다.
#: critical_facts 는 세지 않는다 —
#: 틀린 수치는 이미 그 key_point 의 contradicted 에 반영돼 두 번 세게 된다
CONTENT_BURDEN_STATUSES = frozenset({"missing", "contradicted"})

#: 내용(content_delivery)의 부담은 문제 이름이 없어 문제 자리에 이 이름을 쓴다
CONTENT_ISSUE = "CONTENT"

#: 시간 · 내용은 늘 장 미션 (명세 6-1 7번)
SLIDE_ONLY_ISSUES = frozenset({"SLIDE_OVER", "BEHIND_SCHEDULE", "AHEAD_OF_SCHEDULE", CONTENT_ISSUE})
#: Take 전체의 시간 초과는 늘 Take 미션. 그 구간의 장은 시간을 넘긴 순간에 보던 장일 뿐이다
TAKE_ONLY_ISSUES = frozenset({"TIME_OVER"})

ISSUE_TO_DIMENSION: dict[str, str] = {
    issue: dim for dim, spec in DIMENSIONS.items() for issue in spec.issues
}
METRIC_TO_DIMENSION: dict[str, str] = {
    metric: dim for dim, spec in DIMENSIONS.items() for metric in spec.metrics
}
