"""코치 AI 응답 읽기 · 요약 값.

AI 예시 파일(`ai/service/src/pitch_coach_ai/features/coach/examples/`)을 직접 읽는다.
"""

import copy

import pytest

from pitch_coach_backend.coach.replies import (
    EvaluateReply,
    FinalizeReply,
    PlanReply,
    SummaryMeasures,
    summary_measures,
)
from tests.coach.conftest import PLAN_RESPONSE, example

# ── 응답 읽기 ─────────────────────────────────────────────────────────


def test_evaluate_example_reads_and_keeps_state_and_events_untouched():
    body = example("evaluate/response.json")

    reply = EvaluateReply.model_validate(body)

    assert reply.intervened
    assert reply.feedback is not None and reply.feedback.area == "GAZE"
    assert reply.feedback.evidence == body["feedback"]["evidence"]
    assert reply.indicators == body["indicators"]
    # AI 로 다시 가는 값과 저장할 값은 받은 그대로다
    assert reply.coach_state == body["coach_state"]
    assert reply.events == body["events"]
    assert reply.meta == body["meta"]


@pytest.mark.parametrize("name", ["finalize/response.json", "finalize/response_replay.json"])
def test_finalize_example_keeps_the_whole_take_result(name: str):
    body = example(name)

    reply = FinalizeReply.model_validate(body)

    assert reply.take_result == body["take_result"]
    assert reply.events == body["events"]
    assert reply.duration_sec == 92


def test_fields_the_ai_adds_survive_until_storage():
    body = example("finalize/response.json")
    body["take_result"]["new_field"] = {"x": 1}
    body["events"][0]["new_field"] = 1
    body["meta"]["new_field"] = "y"
    body["new_top_level"] = True

    reply = FinalizeReply.model_validate(body)

    assert reply.take_result["new_field"] == {"x": 1}
    assert reply.events[0]["new_field"] == 1
    assert reply.meta["new_field"] == "y"


def test_plan_reply_keeps_why_for_storage():
    reply = PlanReply.model_validate(PLAN_RESPONSE)

    assert reply.coaching_plan == PLAN_RESPONSE["coaching_plan"]
    assert (reply.source_take_id, reply.script_version_id) == ("take-122", "sv-3")


def test_summary_measures_from_the_example():
    measures = summary_measures(example("finalize/response.json")["take_result"])

    assert measures == SummaryMeasures(
        average_cpm=300.0,
        filler_count=2,
        audience_gaze=76,
        slide_gaze=0,
        script_gaze=24,
        script_dependency_count=0,
        volume=0.0,
        total_intermission=0,
        # GAZE 0.9674 · 나머지 넷 1.0 의 평균 99.35 를 반올림. TIME 은 넣지 않는다
        overall_confidence=99,
    )


def test_summary_leaves_unmeasured_values_empty():
    take_result = copy.deepcopy(example("finalize/response.json")["take_result"])
    take_result["areas"]["GAZE"].update(
        {"measured_ratio": 0.1, "take": None, "unmeasured_reason": "LOW_MEASURED_RATIO"}
    )
    take_result["areas"]["SPEED"]["take"]["cpm"] = None
    del take_result["areas"]["FILLER"]

    measures = summary_measures(take_result)

    assert (measures.audience_gaze, measures.script_dependency_count) == (None, None)
    assert measures.average_cpm is None
    assert measures.filler_count is None
    # 측정 비율은 낮아도 있으면 평균에 들어간다 (0.1 · 1.0 · 1.0 · 1.0 → 77.5, .5 는 올린다)
    assert measures.overall_confidence == 78


@pytest.mark.parametrize(
    ("ratio", "percent"),
    [
        (0.125, 13),
        (0.285, 29),
        (0.145, 15),
        (0.565, 57),
        (0.0, 0),
        (1, 100),
        (0.621, 62),
        (0.624, 62),
        (0.625, 63),
    ],
)
def test_summary_rounds_half_up(ratio: float, percent: int):
    # 0.285 * 100 은 이진 소수로 28.499… 라 floor(x + 0.5) 로는 28 이 된다
    take_result = copy.deepcopy(example("finalize/response.json")["take_result"])
    take_result["areas"]["GAZE"]["take"]["audience_ratio"] = ratio
    take_result["areas"]["GAZE"]["take"]["reading_runs"] = 2.5

    measures = summary_measures(take_result)

    assert (measures.audience_gaze, measures.script_dependency_count) == (percent, 3)


def test_summary_drops_values_that_do_not_fit_their_column():
    """칸에 안 들어가는 값을 넣으면 Take 결과 저장이 통째로 실패한다. 그 칸만 비운다."""
    take_result = copy.deepcopy(example("finalize/response.json")["take_result"])
    take_result["areas"]["VOLUME"]["take"]["voice_diff_db"] = 1_000.0
    take_result["areas"]["FILLER"]["take"]["filler_count"] = 2**31
    take_result["areas"]["GAZE"]["take"]["audience_ratio"] = 1e9

    measures = summary_measures(take_result)

    assert (measures.volume, measures.filler_count, measures.audience_gaze) == (None, None, None)
    # 범위 안의 값은 그대로다
    take_result["areas"]["VOLUME"]["take"]["voice_diff_db"] = -999.99
    assert summary_measures(take_result).volume == -999.99
