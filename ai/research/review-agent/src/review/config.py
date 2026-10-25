"""리뷰 설정 — 판단 기준값 · 가중치 · 다음 미션 규칙.

**모든 숫자는 잠정입니다.** 명세에는 숫자가 없고(config 에만 둔다),
실제 발표 데이터로 검증하지 않았습니다.
손으로 고른 시작점이며 공통 기준 Take 로 조정합니다.

기본값은 이 파일에만 있습니다. 바꿀 때는 코드를 고치지 말고 바꿀 값만 덮어쓰세요 (load_config).
코어는 설정 파일을 읽지 않습니다.
응답 meta 의 criteria_versions.review 가 어떤 설정으로 판단했는지 기록합니다.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from .version import FEATURE_VERSION


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluableConfig(_Section):
    """1단계: 판단 가능 여부"""

    # 영역 측정 비율 기준은 두지 않는다: 코치의 areas.<영역>.unmeasured_reason 을 따른다
    #: 대본 전달도 take_scores.scored_ratio 가 이 값 미만이면 content_delivery 가 NOT_EVALUABLE
    min_scored_ratio: float = 0.5


class BurdenConfig(_Section):
    """2단계: 문제 부담 (mean_severity × 초)"""

    #: missing · contradicted 핵심 내용 하나의 부담. content_coverage 가중치와 같은 3:2:1
    content_burden: dict[str, float] = Field(
        default_factory=lambda: {"critical": 9.0, "high": 6.0, "normal": 3.0}
    )
    #: importance 가 없거나 모르는 값일 때 쓰는 중요도
    content_burden_fallback: str = "normal"
    #: 직전 반복 문제였던 차원의 부담 배수
    recurring_weight: float = 1.2
    #: 코치가 포기한 문제(gave_up)의 차원 부담 배수. 반복 문제보다 작게 둔다
    gave_up_weight: float = 1.1
    #: 미션을 이루지 못한(FAILED · PARTIAL) 지표의 차원 부담 배수. 반복 문제보다 작게 둔다
    mission_failed_weight: float = 1.1


class DimensionConfig(_Section):
    """3단계: 차원 상태"""

    #: PRIORITY 후보가 되는 최소 부담. 반복 문제였던 차원은 이 값 미만으로 내려와야 IMPROVED
    priority_burden_min: float = 10.0
    #: PRIORITY 최대 개수 (부담 큰 순)
    priority_max_count: int = 3


class MissionConfig(_Section):
    """4 · 6단계: 미션 · 사용자 기준 판정"""

    #: 못 미친 정도(절대값)가 이 안이면 PARTIAL. 개수 지표 · 목록에 없는 지표는 허용치가 없다
    tolerance: dict[str, float] = Field(
        default_factory=lambda: {
            "audience_ratio": 0.05,
            "script_ratio": 0.05,
            "reading_ratio": 0.05,
            "cpm": 20.0,
            "voice_diff_db": 1.0,
            "low_ratio": 0.05,
            "filler_per_min": 0.5,
            "content_coverage": 0.05,
        }
    )


class MissionRule(_Section):
    """문제 하나의 다음 미션 지표와 비교 방향"""

    metric: str
    operator: Literal["LTE", "GTE"]


class NextMissionConfig(_Section):
    """7단계: 다음 미션"""

    #: 문제별 미션 지표 · 비교 방향 (명세 6-1 7번). PRIORITY 차원에서 고른 문제로 찾는다.
    #: 빠름 · 느림처럼 같은 지표라도 문제마다 방향이 다르다.
    #: 내용 전달은 문제 이름이 없어 vocab.CONTENT_ISSUE 로 찾는다.
    #: 표에 없는 문제는 후보를 만들지 않는다
    mission_target: dict[str, MissionRule] = Field(
        default_factory=lambda: {
            "GAZE_LOW_EYE_CONTACT": MissionRule(metric="audience_ratio", operator="GTE"),
            "GAZE_AWAY": MissionRule(metric="audience_ratio", operator="GTE"),
            "GAZE_ON_SCRIPT": MissionRule(metric="script_ratio", operator="LTE"),
            "PACE_FAST": MissionRule(metric="cpm", operator="LTE"),
            "PACE_SLOW": MissionRule(metric="cpm", operator="GTE"),
            "LONG_SILENCE": MissionRule(metric="long_silence_count", operator="LTE"),
            "BEHIND_SCHEDULE": MissionRule(metric="slide_duration_ms", operator="LTE"),
            "AHEAD_OF_SCHEDULE": MissionRule(metric="slide_duration_ms", operator="GTE"),
            "SLIDE_OVER": MissionRule(metric="slide_duration_ms", operator="LTE"),
            "TIME_OVER": MissionRule(metric="duration_ms", operator="LTE"),
            "VOLUME_LOW": MissionRule(metric="voice_diff_db", operator="GTE"),
            "FILLER_FREQUENT": MissionRule(metric="filler_per_min", operator="LTE"),
            "CONTENT": MissionRule(metric="content_coverage", operator="GTE"),
        }
    )
    #: 지금 값에서 나아질 폭 (크기). 방향은 mission_target 의 operator 다
    step: dict[str, int | float] = Field(
        default_factory=lambda: {
            "audience_ratio": 0.1,
            "script_ratio": 0.1,
            "cpm": 20,
            "long_silence_count": 1,
            "slide_duration_ms": 10_000,
            "duration_ms": 30_000,
            "voice_diff_db": 1.0,
            "filler_per_min": 0.5,
            "filler_count": 2,
            "content_coverage": 0.1,
        }
    )
    #: 지표별 최종 목표. 다음 목표는 이 값을 넘지 않고,
    #: 이미 넘었으면 그 지표로 미션을 만들지 않는다.
    #: 기준이 Take 마다 다른 지표(cpm · slide_duration_ms · duration_ms)는 여기에 두지 않고
    #: 그 Take 의 기준을 쓴다 (next_missions._final)
    final: dict[str, int | float] = Field(
        default_factory=lambda: {
            "audience_ratio": 0.8,
            "script_ratio": 0.2,
            "long_silence_count": 0,
            "voice_diff_db": 0.0,
            "filler_per_min": 1.0,
            "filler_count": 3,
            "content_coverage": 0.9,
        }
    )
    #: 부담이 한 장에 이 비율을 넘게 몰리면 장 미션 (반복 문제의 장도 같은 규칙)
    slide_concentration: float = 0.5
    #: PRIORITY 차원에서 만드는 후보 수 (Take · 장 미션 합쳐서).
    #: 실패한 사용자 목표 미션은 이 수와 상관없이 모두 넣고, 합계 상한은 schemas.MAX_NEXT_MISSIONS
    priority_candidate_max: int = 3


class ComparisonConfig(_Section):
    """6-1 6번: 이전 Take 비교"""

    #: 비교하는 지표와 좋은 방향. 표에 없는 지표는 비교하지 않는다.
    #: HIGHER · LOWER: 한 방향.
    #: RANGE: 각 Take 의 값이 그 Take 의 기준에 가까워졌는지
    #: (cpm: criteria 의 PACE_SLOW ~ PACE_FAST 기준, duration_ms: plan,
    #: slide_duration_ms: 장 target_ms).
    #: None: 방향을 정할 수 없음 (improved null)
    direction: dict[str, Literal["HIGHER", "LOWER", "RANGE"] | None] = Field(
        default_factory=lambda: {
            "audience_ratio": "HIGHER",
            "script_ratio": "LOWER",
            "reading_ratio": "LOWER",
            "reading_runs": "LOWER",
            "cpm": "RANGE",
            # 말 사이 멈춤은 많고 적음 중 어느 쪽이 좋은지 정하지 않았다
            "pause_count": None,
            "long_silence_count": "LOWER",
            "duration_ms": "RANGE",
            "slide_duration_ms": "RANGE",
            # 작은 소리(VOLUME_LOW)만 문제로 보고, 다음 미션도 GTE 다
            "voice_diff_db": "HIGHER",
            "low_ratio": "LOWER",
            "filler_per_min": "LOWER",
            "filler_count": "LOWER",
            "content_coverage": "HIGHER",
            "critical_fact_accuracy": "HIGHER",
        }
    )


class ScoreConfig(_Section):
    """9단계: 점수. NOT_EVALUABLE 을 뺀 평균"""

    state_score: dict[str, int] = Field(
        default_factory=lambda: {"STRENGTH": 100, "IMPROVED": 85, "STABLE": 70, "PRIORITY": 40}
    )


class ReviewConfig(_Section):
    evaluable: EvaluableConfig = Field(default_factory=EvaluableConfig)
    burden: BurdenConfig = Field(default_factory=BurdenConfig)
    dimension: DimensionConfig = Field(default_factory=DimensionConfig)
    mission: MissionConfig = Field(default_factory=MissionConfig)
    next_mission: NextMissionConfig = Field(default_factory=NextMissionConfig)
    comparison: ComparisonConfig = Field(default_factory=ComparisonConfig)
    score: ScoreConfig = Field(default_factory=ScoreConfig)

    _hash: str | None = PrivateAttr(default=None)

    def config_hash(self) -> str:
        """재현성 키. 같은 hash 면 같은 입력에 같은 결론이 나온다.

        설정은 만든 뒤 바꾸지 않는다는 전제로 한 번만 계산한다.
        """
        if self._hash is None:
            blob = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
            self._hash = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
        return self._hash

    def criteria_version(self) -> str:
        """<feature_version>+<설정 해시 12자리>. 응답 meta.criteria_versions.review"""
        return f"{FEATURE_VERSION}+{self.config_hash()}"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(**override: Any) -> ReviewConfig:
    """기본값 위에 키워드 인자를 덮어쓴다. 예: ``load_config(dimension={"priority_max_count": 2})``

    덮어쓰지 않은 값은 기본값이 남는다. 파일은 읽지 않는다.
    """
    merged = ReviewConfig().model_dump(mode="json")
    if override:
        merged = _deep_merge(merged, override)
    return ReviewConfig.model_validate(merged)


DEFAULT_CONFIG = ReviewConfig()
