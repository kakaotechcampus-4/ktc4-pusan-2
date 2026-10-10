"""복구 — 같은 Take 를 정상 · 응답 누락 · replay 로 돌려 Take 결과가 같은지 본다."""

from __future__ import annotations

from coach import finalize
from coach_lab.judges import lab_judges
from coach_lab.paths import SCENARIOS_DIR
from coach_lab.recovery_eval import DROPS, compare, full_replay, offline
from coach_lab.simulator import Scenario, run

SCENARIO = SCENARIOS_DIR / "02_gaze_effective.json"


def _segments(take) -> list[tuple]:
    return [
        (s.issue_type.value, s.slide_number, s.start_ms, s.end_ms, s.reliable, s.coached)
        for s in take.problem_segments
    ]


def test_full_replay_rebuilds_the_same_take_result():
    result = run(Scenario.load(SCENARIO))
    replayed = full_replay(result)
    assert replayed.replayed is True
    assert replayed.areas == result.take_result.areas
    assert _segments(replayed) == _segments(result.take_result)


def test_rerunning_the_same_raw_data_gives_the_same_take():
    result = run(Scenario.load(SCENARIO))
    take, used = offline(result)
    assert used is False
    assert take.areas == result.take_result.areas
    assert _segments(take) == _segments(result.take_result)


def test_missing_responses_longer_than_the_window_need_replay():
    result = run(Scenario.load(SCENARIO))
    take, used = offline(result, drop=DROPS["long_drop"])
    assert used is True and take.replayed is True
    # 원자료로 다시 판정하므로 Take 지표는 정상 재생과 같다
    assert take.areas == result.take_result.areas


def test_missing_responses_shorter_than_the_window_are_filled_by_the_next_window():
    result = run(Scenario.load(SCENARIO))
    take, used = offline(result, drop=DROPS["short_drop"])
    assert used is False and take.replayed is False
    diff = compare(result.take_result, take)["area_diff"]
    assert all(v == 0 for metrics in diff.values() for v in metrics.values())


def test_empty_raw_data_is_not_shown_as_measured():
    result = run(Scenario.load(SCENARIO))
    empty = {
        "gaze_records": [],
        "voice_records": [],
        "stt_status_changes": [],
        "words": [],
        "utterance_ends": [],
        "slides": [],
    }
    req = {**result.final_request, "coach_state": None, "replay": empty}
    areas = finalize(req, lab_judges(), result.config).take_result.areas
    for area in ("GAZE", "SPEED", "VOLUME", "FILLER"):
        assert areas[area].unmeasured_reason == "LOW_MEASURED_RATIO", area


def test_compare_counts_values_present_on_one_side_only():
    result = run(Scenario.load(SCENARIO))
    normal = result.take_result
    gone = normal.model_copy(deep=True)
    gone.areas["GAZE"].take = None
    gone.areas["GAZE"].unmeasured_reason = "LOW_MEASURED_RATIO"
    words = normal.model_copy(deep=True)
    words.areas["FILLER"].take = {**words.areas["FILLER"].take, "by_word": {"음": 99}}
    assert compare(normal, normal)["values_missing"] == 0
    assert compare(normal, gone)["values_missing"] > 0
    diff = compare(normal, words)
    assert diff["values_missing"] > 0 or any(
        v > 0 for k, v in diff["area_diff"].get("FILLER", {}).items() if k.startswith("by_word")
    )
