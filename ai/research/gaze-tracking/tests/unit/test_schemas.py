"""입출력 계약(gaze.schemas)과 코어 출력이 맞는지 검사한다.

* 입력 1초 기록은 필드 8개가 전부다. 얼굴 영상 · 랜드마크 같은 값이 섞여 와도 버린다;
* 잘못된 기록(모르는 상태, OTHER 가 아닌데 방향, 범위 밖 값)은 거절한다;
* 코어가 돌려주는 dict 는 출력 모델과 키까지 맞는다 (무작위 타임라인 여러 개로 확인).
"""

from __future__ import annotations

import random

import pytest
from pydantic import ValidationError

from gaze.config import EvidenceConfig
from gaze.core import (
    GAZE_DIRECTIONS,
    SAMPLE_STATES,
    GazeSample,
    GazeTimeline,
    compare_summaries,
    evaluate_gaze,
    intervention_outcome,
    take_summary,
)
from gaze.schemas import (
    GazeInterventionOutcome,
    GazeIssue,
    GazeSampleRecord,
    GazeSummaryComparison,
    GazeTakeSummary,
)

CFG = EvidenceConfig()
RECORD = {
    "t_ms": 14000,
    "duration_ms": 1000,
    "state": "OTHER",
    "direction": "UP_LEFT",
    "confidence": 1.0,
    "reliability": 0.9475,
    "issues": [],
    "frames": 7,
}


# ==========================================================================
# 입력: 1초 기록
# ==========================================================================


def test_record_fields_are_exactly_the_wire_dict():
    wire = GazeSample(0, 1000, "CAMERA").to_dict()
    assert set(GazeSampleRecord.model_fields) == set(wire)


def test_record_becomes_the_same_core_sample():
    record = GazeSampleRecord.model_validate(RECORD)
    assert record.to_sample() == GazeSample.from_dict(RECORD)
    assert record.to_sample().to_dict() == RECORD


def test_unknown_fields_are_dropped():
    record = GazeSampleRecord.model_validate(
        {**RECORD, "landmarks": [[0.5, 0.5, 0.0]], "frame": "..."}
    )
    assert set(record.model_dump()) == set(RECORD)


@pytest.mark.parametrize(
    "change",
    [
        {"state": "LOOKING"},
        {"confidence": 1.5},
        {"reliability": -0.1},
        {"t_ms": -1},
        {"duration_ms": 0},
        {"frames": -1},
    ],
)
def test_bad_records_are_rejected(change):
    with pytest.raises(ValidationError):
        GazeSampleRecord.model_validate({**RECORD, **change})


def test_a_direction_on_a_non_other_record_is_dropped_not_rejected():
    # 방향만 뜻이 없을 뿐 상태 · 신뢰도는 쓸 수 있는 정보다. 기록 전체를 버리지 않는다
    record = GazeSampleRecord.model_validate({**RECORD, "state": "CAMERA"})
    assert (record.state, record.direction) == ("CAMERA", None)
    assert record.reliability == RECORD["reliability"]


def test_an_unknown_direction_or_null_issues_keep_the_record():
    record = GazeSampleRecord.model_validate({**RECORD, "direction": "NORTH", "issues": None})
    assert (record.state, record.direction, record.issues) == ("OTHER", None, [])


def test_missing_counts_default_like_the_core():
    minimal = {"t_ms": 0, "duration_ms": 1000, "state": "UNMEASURED"}
    assert GazeSampleRecord.model_validate(minimal).to_sample() == GazeSample.from_dict(minimal)


def test_missing_required_fields_are_rejected():
    for key in ("t_ms", "duration_ms", "state"):
        with pytest.raises(ValidationError):
            GazeSampleRecord.model_validate({k: v for k, v in RECORD.items() if k != key})


# ==========================================================================
# 출력: 코어 dict ↔ 출력 모델
# ==========================================================================


def _timeline(rng: random.Random, n: int) -> GazeTimeline:
    samples, t, state = [], rng.choice([0, 3000]), "CAMERA"
    for _ in range(n):
        if rng.random() < 0.35:
            state = rng.choice(SAMPLE_STATES)
        direction = rng.choice(GAZE_DIRECTIONS) if state == "OTHER" else None
        issues = tuple(rng.sample(["TOO_FAR", "SECOND_FACE", "MOVED_TOO_FAR"], rng.randint(0, 1)))
        samples.append(
            GazeSample(
                t, 1000, state, direction, rng.random(), rng.random(), issues, rng.randint(0, 8)
            )
        )
        t += 1000 + (2000 if rng.random() < 0.03 else 0)
    return GazeTimeline(samples)


TIMELINES = [_timeline(random.Random(seed), n) for seed, n in enumerate([0, 1, 5, 40, 120] * 8)]


@pytest.mark.parametrize("timeline", TIMELINES)
def test_core_outputs_match_the_output_models(timeline):
    end = timeline.end_ms or 0
    for t in range(timeline.start_ms or 0, end + 3000, 1000):
        for issue in evaluate_gaze(timeline, t, CFG):
            GazeIssue.model_validate(issue)
        for issue_type in ("GAZE_ON_SCRIPT", "GAZE_ON_SCREEN", "GAZE_AWAY", "GAZE_LOW_EYE_CONTACT"):
            GazeInterventionOutcome.model_validate(
                intervention_outcome(timeline, t, issue_type, CFG)
            )
    summary = take_summary(timeline, CFG)
    GazeTakeSummary.model_validate(summary)
    if timeline.samples:  # every key, not only the empty-take ones
        assert set(summary) == set(GazeTakeSummary.model_fields)
    GazeSummaryComparison.model_validate(
        compare_summaries(take_summary(TIMELINES[3], CFG), summary)
    )
    GazeSummaryComparison.model_validate(
        compare_summaries(take_summary(GazeTimeline(), CFG), summary)
    )


def test_output_models_reject_an_extra_key():
    (issue,) = evaluate_gaze(_script_run(), 9000, CFG)
    with pytest.raises(ValidationError):
        GazeIssue.model_validate({**issue, "landmarks": []})


def test_per_state_maps_need_every_key():
    summary = take_summary(_script_run(), CFG)
    comparison = compare_summaries(summary, summary)
    del comparison["state_ratio"]["OTHER"]
    with pytest.raises(ValidationError):
        GazeSummaryComparison.model_validate(comparison)
    del summary["other_direction_ms"]["UP"]
    with pytest.raises(ValidationError):
        GazeTakeSummary.model_validate(summary)


def _script_run() -> GazeTimeline:
    states = ["CAMERA"] * 5 + ["BOTTOM"] * 4
    return GazeTimeline(
        [GazeSample(i * 1000, 1000, s, None, 1.0, 1.0) for i, s in enumerate(states)]
    )
