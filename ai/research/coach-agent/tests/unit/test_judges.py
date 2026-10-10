"""판정 라운드(judges.run) — 가짜 판정 모듈로 코치 규칙만 시험한다."""

from __future__ import annotations

from typing import Any

import pytest

from coach import judges as judges_mod
from coach.config import DEFAULT_CONFIG
from coach.judges import Judges, run
from coach.schemas import CoachRequest
from coach.state import CoachState, initial_state
from coach.vocab import FeedbackType
from tests.unit.fakes import FakeJudge, fake_judges

PLAN = {
    "target_ms": 120_000,
    "min_ms": 110_000,
    "max_ms": 130_000,
    "slides": [
        {"slide_number": 1, "target_ms": 60_000, "script_chars": 300},
        {"slide_number": 2, "target_ms": 60_000, "script_chars": 300},
    ],
}


def word(text: str, start: int, end: int) -> dict[str, Any]:
    return {"word": text, "start_ms": start, "end_ms": end}


def voice(t_end: int, *, level: float | None = -30.0, live: bool = True) -> dict[str, Any]:
    """t_end 에 끝나는 1초 음량 기록."""
    return {"t_ms": t_end - 1_000, "level_db": level, "voiced_ms": 800, "audio_live": live}


def req(
    t: int,
    *,
    words: list[dict[str, Any]] | None = None,
    voices: list[dict[str, Any]] | None = None,
    slide: tuple[int, int] | None = (1, 0),
    stt: str = "ok",
    base_db: float | None = None,
) -> CoachRequest:
    inputs: dict[str, Any] = {
        "gaze_records": [{"t_ms": t - 1_000, "state": "CAMERA"}],
        "voice_records": [voice(t)] if voices is None else voices,
        "words": words or [],
        "stt_status": stt,
        "slide": {"number": slide[0], "started_ms": slide[1]} if slide else None,
    }
    return CoachRequest.model_validate(
        {
            "take_id": "t",
            "t_ms": t,
            "plan": PLAN,
            "inputs": inputs,
            "calibration": {"base_level_db": base_db},
        }
    )


def go(r: CoachRequest, state: CoachState, judges: Judges):
    return run(r, state, judges, DEFAULT_CONFIG)


def inputs_of(judge: Any, call: int = -1) -> dict[str, Any]:
    return judge.calls[call][0]


def test_requires_inputs():
    r = CoachRequest.model_validate({"take_id": "t", "t_ms": 1000})
    with pytest.raises(ValueError):
        go(r, initial_state(), fake_judges())


def test_call_order_and_module_inputs():
    order: list[str] = []
    judges = fake_judges(order=order)
    state = initial_state()
    words = [word("안녕", 100, 400), word("하세요", 500, 900)]
    out = go(req(1_000, words=words), state, judges)

    assert order == ["filler", "pace", "gaze", "volume"]
    assert set(out.results) == {
        FeedbackType.GAZE,
        FeedbackType.SPEED,
        FeedbackType.VOLUME,
        FeedbackType.PAUSE,
        FeedbackType.FILLER,
        FeedbackType.TIME,
    }
    assert out.results[FeedbackType.TIME].evaluator == "timing"
    first = inputs_of(judges.filler)
    assert first["since_ms"] == 0 and first["words_since_ms"] == -1
    assert first["stt_status"] == "ok" and first["stt_ok_since_ms"] == 0
    assert first["words"] == words
    assert inputs_of(judges.gaze)["voiced"] == [{"t_ms": 0, "voiced_ms": 800}]
    assert inputs_of(judges.volume)["records"][0]["level_db"] == -30.0
    assert inputs_of(judges.volume)["base_level_db"] is None

    # 두 번째 라운드는 돌려준 커서로 부른다
    go(req(2_000, words=words), state, judges)
    second = inputs_of(judges.filler)
    assert second["since_ms"] == 1_000 and second["words_since_ms"] == 900
    assert inputs_of(judges.gaze)["since_ms"] == 1_000
    assert inputs_of(judges.volume)["since_ms"] == 1_000


def test_stt_ok_since_set_on_recovery():
    judges = fake_judges()
    state = initial_state()
    out = go(req(1_000, stt="reconnecting"), state, judges)
    assert not out.stt_ok and state.stt_gap
    # 모듈은 BE 의 상태를 그대로 받는다
    assert inputs_of(judges.pace)["stt_status"] == "reconnecting"
    go(req(2_000), state, judges)
    assert state.stt_ok_since_ms == 2_000 and not state.stt_gap
    assert inputs_of(judges.pace)["stt_ok_since_ms"] == 2_000


def test_pace_gets_true_and_null_fillers_only():
    judges = fake_judges()
    words = [word("음", 0, 100), word("어", 200, 300), word("네", 400, 500)]
    go(req(1_000, words=words), initial_state(), judges)
    assert inputs_of(judges.pace)["fillers"] == [
        {"start_ms": 0, "end_ms": 100, "is_filler": True},
        {"start_ms": 200, "end_ms": 300, "is_filler": None},
    ]


def test_filler_failure_gives_pace_null_and_skips_stt_ok_time():
    judges = fake_judges(filler=FakeJudge("filler", raise_at={2_000}))
    state = initial_state()
    go(req(1_000), state, judges)
    out = go(req(2_000), state, judges)
    assert out.failed == {"filler"}
    assert inputs_of(judges.pace)["fillers"] is None
    # 머문 시간은 세고, STT 를 믿은 시간에는 넣지 않는다
    assert state.slide_totals["1"]["TIME"]["elapsed_ms"] == 2_000
    assert state.slide_stt_ok_ms["1"] == 1_000


def test_pace_failure_skips_stt_ok_time():
    judges = fake_judges(pace=FakeJudge("pace", raise_at={1_000}))
    state = initial_state()
    out = go(req(1_000), state, judges)
    assert out.failed == {"pace"}
    assert state.slide_totals["1"]["TIME"]["elapsed_ms"] == 1_000
    assert state.slide_stt_ok_ms.get("1", 0) == 0
    # pace 가 실패하면 timing 은 속도를 모른다
    assert out.results[FeedbackType.SPEED].metrics == {}


def test_module_exception_isolated_with_fallback():
    judges = fake_judges(gaze=FakeJudge("gaze", raise_at={3_000}))
    state = initial_state()
    for t in (1_000, 2_000):
        go(req(t), state, judges)
    out = go(req(3_000), state, judges)

    gaze = out.results[FeedbackType.GAZE]
    assert out.failed == {"gaze"}
    assert gaze.measurable is False and gaze.state == "UNMEASURABLE"
    assert gaze.issues == [] and gaze.metrics == {}
    # 처음 본 버전을 쓰고, 커서는 이번 창 끝으로 넘어가며, 그 시간은 total_ms 에만 센다
    assert gaze.criteria_version == "gaze-fake-1"
    assert state.cursors["gaze"].since_ms == 3_000
    assert state.totals["GAZE"] == {"total_ms": 3_000}
    # 다른 모듈은 그대로 돈다
    assert out.results[FeedbackType.VOLUME].measurable is True
    assert state.totals["TIME"]["elapsed_ms"] == 3_000


def test_fallback_version_unknown_and_words_cursor():
    judges = fake_judges(filler=FakeJudge("filler", raise_at={1_000}))
    state = initial_state()
    out = go(req(1_000, words=[word("가", 100, 700)]), state, judges)
    filler = out.results[FeedbackType.FILLER]
    assert filler.criteria_version == "filler-unknown"
    assert filler.words_counted_until_ms == 700
    assert state.cursors["filler"].words_since_ms == 700
    assert state.totals["FILLER"] == {"total_ms": 1_000}


def test_volume_failure_gives_two_fallback_results():
    judges = fake_judges(volume=FakeJudge("volume", raise_at={1_000}))
    state = initial_state()
    out = go(req(1_000), state, judges)
    assert out.failed == {"volume"}
    for area in (FeedbackType.VOLUME, FeedbackType.PAUSE):
        assert out.results[area].measurable is False
        assert out.results[area].state == "UNKNOWN"
        assert state.totals[area.value] == {"total_ms": 1_000}
    assert state.cursors["volume"].since_ms == 1_000


def test_timing_failure_counts_elapsed(monkeypatch):
    def boom(*args: Any, **kwargs: Any):
        raise RuntimeError("timing 가짜 예외")

    monkeypatch.setattr(judges_mod, "timing_judge", boom)
    state = initial_state()
    out = go(req(2_000), state, fake_judges())
    assert out.failed == {"timing"}
    assert out.results[FeedbackType.TIME].measurable is False
    assert state.totals["TIME"] == {"elapsed_ms": 2_000}
    assert state.cursors["timing"].since_ms == 2_000


def test_cursors_prevent_double_counting():
    judges = fake_judges()
    state = initial_state()
    words = [word("안녕", 100, 400)]
    go(req(1_000, words=words), state, judges)
    go(req(2_000, words=words), state, judges)  # 겹친 창으로 같은 단어를 다시 받는다
    assert state.totals["GAZE"]["total_ms"] == 2_000
    assert state.totals["SPEED"]["chars"] == 2
    # 같은 요청을 한 번 더 보내도 합계는 그대로
    go(req(2_000, words=words), state, judges)
    assert state.totals["GAZE"]["total_ms"] == 2_000
    assert state.totals["TIME"]["elapsed_ms"] == 2_000


def test_three_second_gap_counted_once():
    judges = fake_judges()
    state = initial_state()
    go(req(1_000), state, judges)
    go(req(4_000), state, judges)
    assert state.totals["GAZE"]["total_ms"] == 4_000
    assert state.totals["VOLUME"]["total_ms"] == 4_000
    assert state.totals["TIME"]["elapsed_ms"] == 4_000


def test_late_word_lands_in_slide_of_speaking_time():
    judges = fake_judges()
    state = initial_state()
    go(req(1_000, slide=(1, 0)), state, judges)
    # 2장으로 넘어간 뒤에야 확정된, 1장에서 말한 단어
    late = word("안녕하세요", 4_500, 4_900)
    now = word("네", 5_200, 5_400)
    go(req(6_000, slide=(2, 5_000), words=[late, now]), state, judges)
    assert state.slide_totals["1"]["SPEED"]["chars"] == 5
    assert state.slide_totals["2"]["SPEED"]["chars"] == 1
    assert state.totals["SPEED"]["chars"] == 6
    # 머문 시간도 장 시작에서 끊긴다
    assert state.slide_totals["1"]["TIME"]["elapsed_ms"] == 5_000
    assert state.slide_totals["2"]["TIME"]["elapsed_ms"] == 1_000
    assert state.slide_log == [(1, 0), (2, 5_000)]


def test_baseline_collected_until_value_then_fixed():
    judges = fake_judges()
    state = initial_state()
    levels = [-30.0, -32.0, -31.0]
    records = []
    for i, level in enumerate(levels, start=1):
        records.append(voice(i * 1_000, level=level))
        # 창에는 지난 기록도 같이 온다 — 커서 이전 것은 표본에 다시 넣지 않는다
        go(req(i * 1_000, voices=list(records)), state, judges)
        if i < 3:
            assert state.baseline_samples == levels[:i]
            assert inputs_of(judges.volume)["base_level_db"] is None
    assert state.base_level_db == -31.0 and state.base_level_source == "TAKE"
    assert state.baseline_samples == []
    assert inputs_of(judges.volume)["base_level_db"] == -31.0
    go(req(4_000, voices=[voice(4_000)]), state, judges)
    assert state.baseline_samples == []
    assert inputs_of(judges.volume)["base_level_db"] == -31.0


def test_null_level_records_are_not_baseline_samples():
    state = initial_state()
    go(req(1_000, voices=[voice(1_000, level=None)]), state, fake_judges())
    assert state.baseline_samples == []


def test_calibration_overrides_baseline():
    judges = fake_judges()
    state = initial_state()
    go(req(1_000, base_db=-25.0), state, judges)
    assert inputs_of(judges.volume)["base_level_db"] == -25.0
    assert state.base_level_source == "CALIBRATION"
    assert state.baseline_samples == []


def test_audio_dead_keeps_dwell_but_not_stt_ok_time():
    judges = fake_judges()
    state = initial_state()
    out = go(req(1_000, voices=[voice(1_000, live=False)]), state, judges)
    assert out.audio_dead and not out.stt_ok
    assert inputs_of(judges.pace)["stt_status"] == "ok"
    assert state.slide_totals["1"]["TIME"]["elapsed_ms"] == 1_000
    assert state.slide_stt_ok_ms.get("1", 0) == 0

    # 돌아온 뒤의 1초부터 믿는다
    go(req(2_000), state, judges)
    assert state.stt_ok_since_ms == 2_000
    assert state.slide_stt_ok_ms.get("1", 0) == 0
    go(req(3_000), state, judges)
    assert state.slide_stt_ok_ms["1"] == 1_000
    assert state.slide_totals["1"]["TIME"]["elapsed_ms"] == 3_000


def test_covered_merges_and_leaves_gap_after_jump():
    judges = fake_judges()
    state = initial_state()
    for t in range(1_000, 6_000, 1_000):
        go(req(t), state, judges)
    assert state.covered == [[0, 5_000]]
    go(req(45_000), state, judges)
    # 창(30초)보다 오래 비어 [5000, 15000) 은 셀 수 없었다
    assert state.covered == [[0, 5_000], [15_000, 45_000]]


def test_timing_receives_totals_and_pace_threshold(monkeypatch):
    seen: list[dict[str, Any]] = []
    real = judges_mod.timing_judge

    def spy(inputs: dict[str, Any], t_ms: int, config: Any):
        seen.append(inputs)
        return real(inputs, t_ms, config)

    monkeypatch.setattr(judges_mod, "timing_judge", spy)
    judges = fake_judges()
    state = initial_state()
    go(req(1_000, words=[word("안녕하세요", 100, 500)]), state, judges)
    go(req(2_000), state, judges)

    first, second = seen
    assert first["slide_chars"] == {} and first["slide_dwell_ms"] == {}
    assert second["since_ms"] == 1_000 and second["t_ms"] == 2_000
    assert second["slide_chars"] == {"1": 5}
    assert second["slide_dwell_ms"] == {"1": 1_000}
    assert second["slide_stt_ok_ms"] == {"1": 1_000}
    assert second["pace"] == {"cpm": 300.0, "measurable": True, "fast_threshold": 350.0}
    assert second["slide"] == {"number": 1, "started_ms": 0}
    assert second["plan"]["target_ms"] == 120_000


def test_versions_recorded_first_and_latest():
    judges = fake_judges()
    state = initial_state()
    go(req(1_000), state, judges)
    assert state.criteria_versions["gaze"] == "gaze-fake-1"
    assert state.criteria_versions["timing"].startswith("timing-")
    state.criteria_versions["gaze"] = "gaze-old"
    go(req(2_000), state, judges)
    assert state.criteria_versions["gaze"] == "gaze-old"
    assert state.latest_criteria_versions["gaze"] == "gaze-fake-1"


def _result(name: str, area: str, t: int, **extra: Any) -> dict[str, Any]:
    return {
        "evaluator": name,
        "area": area,
        "t_ms": t,
        "counted_until_ms": t,
        "criteria_version": f"{name}-fake-1",
        "measurable": True,
        "state": "NORMAL",
        **extra,
    }


def test_malformed_filler_words_count_as_filler_failure():
    bad = FakeJudge(
        "filler",
        script=lambda i, t: [
            _result("filler", "FILLER", t, words_counted_until_ms=700, words=[{"is_filler": True}])
        ],
    )
    judges = fake_judges(filler=bad)
    state = initial_state()
    out = go(req(1_000, words=[word("음", 100, 700)]), state, judges)
    assert "filler" in out.failed
    assert inputs_of(judges.pace)["fillers"] is None
    assert out.results[FeedbackType.FILLER].measurable is False
    assert state.cursors["filler"].since_ms == 1_000
    assert state.cursors["filler"].words_since_ms == 700


def test_wrong_area_or_evaluator_is_a_module_failure():
    wrong_area = FakeJudge("gaze", script=lambda i, t: [_result("gaze", "TIME", t)])
    state = initial_state()
    out = go(req(1_000), state, fake_judges(gaze=wrong_area))
    assert out.failed == {"gaze"}
    assert out.results[FeedbackType.GAZE].measurable is False
    assert state.totals["TIME"] == {"elapsed_ms": 1_000, "planned_ms": 1_000}
    wrong_name = FakeJudge("gaze", script=lambda i, t: [_result("pace", "GAZE", t)])
    assert go(req(1_000), initial_state(), fake_judges(gaze=wrong_name)).failed == {"gaze"}


def test_duplicate_voice_second_is_one_baseline_sample():
    state = initial_state()
    go(req(1_000, voices=[voice(1_000, level=-30.0)] * 15), state, fake_judges())
    assert state.baseline_samples == [-30.0]
    assert state.base_level_db is None


def test_fallback_time_is_split_at_slide_change():
    judges = fake_judges(gaze=FakeJudge("gaze", raise_at={6_000}))
    state = initial_state()
    go(req(1_000), state, judges)
    go(req(6_000, slide=(2, 5_000)), state, judges)
    # 커서 1초 → 6초 사이의 잴 수 없던 시간: 1장 4초, 2장 1초
    assert state.slide_totals["1"]["GAZE"]["total_ms"] == 5_000
    assert state.slide_totals["2"]["GAZE"]["total_ms"] == 1_000


def test_filler_word_time_overflow_is_a_filler_failure():
    bad = FakeJudge(
        "filler",
        script=lambda i, t: [
            _result(
                "filler",
                "FILLER",
                t,
                words=[{"start_ms": float("inf"), "end_ms": 700, "is_filler": True}],
            )
        ],
    )
    judges = fake_judges(filler=bad)
    out = go(req(1_000), initial_state(), judges)
    assert "filler" in out.failed
    assert inputs_of(judges.pace)["fillers"] is None


def test_baseline_uses_the_first_record_of_a_second():
    state = initial_state()
    first_null = [voice(1_000, level=None), voice(1_000, level=-60.0)]
    go(req(1_000, voices=first_null), state, fake_judges())
    assert state.baseline_samples == []
