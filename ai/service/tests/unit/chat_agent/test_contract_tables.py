"""계약 시험: 리뷰와 같은 표를 쓰는지 (기능 문서 4-3 · 6-3 · 6-4, 7-6 L4-10).

기능 모듈끼리 import 하지 않으므로 코어 config 에 사본을 두고, 시험이 리뷰의 원본과 같은지 본다.
리뷰 쪽 표가 바뀌면 이 시험이 깨지고, 그때 코어 config 를 같이 고친다(feature_version 을 올림).
"""

from __future__ import annotations

from pitch_coach_ai.features.chat_agent import schemas as s
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG
from pitch_coach_ai.features.review import config as review_config
from pitch_coach_ai.features.review import schemas as review_schemas
from pitch_coach_ai.features.review import vocab as review_vocab


def test_fm_L4_10_direction_table_equals_review() -> None:
    assert dict(DEFAULT_CONFIG.direction) == dict(review_config.DEFAULT_CONFIG.comparison.direction)


def test_dimension_table_equals_review() -> None:
    ours = {
        d: (tuple(spec.areas), tuple(spec.metrics), tuple(spec.issues))
        for d, spec in DEFAULT_CONFIG.dimensions.items()
    }
    theirs = {
        d: (tuple(spec.areas), tuple(spec.metrics), tuple(spec.issues))
        for d, spec in review_vocab.DIMENSIONS.items()
    }
    assert ours == theirs
    assert list(ours) == list(theirs)  # 차원 표 순서도 같다


def test_min_scored_ratio_equals_review() -> None:
    assert (
        DEFAULT_CONFIG.min_scored_ratio == review_config.DEFAULT_CONFIG.evaluable.min_scored_ratio
    )


def test_criteria_target_fields_equal_review() -> None:
    assert set(s.CriteriaTarget.model_fields) == set(review_schemas.CriteriaTarget.model_fields)


def test_goal_metrics_are_comparable_metrics() -> None:
    for metric, gm in DEFAULT_CONFIG.goal_metrics.items():
        direction = DEFAULT_CONFIG.direction[metric]
        assert direction is not None
        if direction == "RANGE":
            assert gm.operators == ("in_range",) and gm.min_delta is None
        else:
            assert gm.min_delta is not None and gm.min_delta > 0
            assert gm.operators == (("gt_base",) if direction == "HIGHER" else ("lt_base",))
        assert DEFAULT_CONFIG.dimension_of_metric(metric) is not None
