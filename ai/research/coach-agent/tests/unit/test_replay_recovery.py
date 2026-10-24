"""finalize 복구 — Take 전체 원자료(replay)로 처음부터 다시 판정해 Take 결과를 만든다."""

from __future__ import annotations

from typing import Any

import pytest

import coach.core as core_mod
from coach import ReplayRequired, decide, finalize, recovery
from coach.schemas import FinalizeRequest, ReplayInput

from .conftest import PLAN
from .fakes import ScriptedJudge, fake_issue, fake_judges


def raw(t_end: int = 30_000, **over: Any) -> dict[str, Any]:
    """t_end 까지 1초 기록 · 단어(1초 뒤 확정) · 장 둘(1번 0초, 2번 15초)인 원자료."""
    base: dict[str, Any] = {
        "gaze_records": [
            {"t_ms": t, "duration_ms": 1_000, "state": "CAMERA"} for t in range(0, t_end, 1_000)
        ],
        "voice_records": [
            {"t_ms": t, "duration_ms": 1_000, "level_db": -30.0, "voiced_ms": 800, "silence_ms": 0}
            for t in range(0, t_end, 1_000)
        ],
        "stt_status_changes": [{"t_ms": 0, "status": "ok"}],
        "words": [
            {"word": "가나다", "start_ms": t, "end_ms": t + 600, "final_at_ms": t + 1_600}
            for t in range(0, t_end - 1_000, 1_000)
        ],
        "utterance_ends": [5_000, 20_000],
        "slides": [{"number": 1, "started_ms": 0}, {"number": 2, "started_ms": 15_000}],
    }
    return {**base, **over}


def intervention(t_ms: int, issue: str, area: str, instruction: str, slide: int) -> dict:
    return {
        "kind": "INTERVENTION",
        "event_id": f"iv-{t_ms}",
        "t_ms": t_ms,
        "intervention_id": f"iv-{t_ms}",
        "issue_type": issue,
        "area": area,
        "instruction": instruction,
        "message": "말",
        "priority": 60,
        "confidence": 0.9,
        "reason_codes": [issue],
        "slide_number": slide,
    }


def request(events: list[dict] | None = None, replay: dict | None = None, t_ms: int = 30_000):
    return {
        "take_id": "test-take",
        "t_ms": t_ms,
        "plan": PLAN,
        "calibration": {"base_level_db": -30.0},
        "coach_state": None,
        "events": events or [],
        "replay": raw(t_ms) if replay is None else replay,
    }


# ── 요청 다시 만들기 ─────────────────────────────────────────────────────


def test_request_window_at_a_given_second():
    r = ReplayInput.model_validate(
        raw(
            60_000,
            stt_status_changes=[
                {"t_ms": 0, "status": "ok"},
                {"t_ms": 35_000, "status": "reconnecting"},
                {"t_ms": 45_000, "status": "ok"},
            ],
        )
    )
    inputs = recovery.inputs_at(r, 40_000)
    # 시선 · 음량은 끝난 지 30초 안의 1초 기록: 10초 ~ 39초에 시작한 30개
    assert [g.t_ms for g in inputs.gaze_records] == list(range(10_000, 40_000, 1_000))
    assert len(inputs.voice_records) == 30
    # 단어는 확정된 시각이 지났고 60초 안에 끝난 것: 38초에 시작한 단어는 39.6초에 확정
    assert inputs.words[-1].start_ms == 38_000
    assert all(w.end_ms <= 40_000 for w in inputs.words)
    assert inputs.utterance_ends == [5_000, 20_000]
    assert inputs.stt_status == "reconnecting"
    assert inputs.slide is not None and (inputs.slide.number, inputs.slide.started_ms) == (
        2,
        15_000,
    )
    assert recovery.inputs_at(r, 50_000).stt_status == "ok"
    assert recovery.inputs_at(r, 10_000).slide.number == 1


def shifted(t_end: int, offset: int = 400) -> dict[str, Any]:
    """1초 격자가 offset 만큼 어긋난 원자료: 기록이 [offset, offset+1000), … 이고 t_end 에 끝남."""
    starts = range(offset, t_end - 1_000 + 1, 1_000)
    return raw(
        t_end,
        gaze_records=[{"t_ms": t, "duration_ms": 1_000, "state": "CAMERA"} for t in starts],
        voice_records=[
            {"t_ms": t, "duration_ms": 1_000, "level_db": -30.0, "voiced_ms": 800, "silence_ms": 0}
            for t in starts
        ],
        words=[
            {"word": "가나다", "start_ms": t, "end_ms": t + 600, "final_at_ms": t + 1_600}
            for t in range(offset, t_end - 1_000, 1_000)
        ],
    )


def test_ticks_on_a_thousand_grid_are_every_second():
    r = ReplayInput.model_validate(raw(10_000))
    assert recovery.ticks(r, 10_000) == list(range(0, 10_000, 1_000))


def test_ticks_follow_the_ends_of_a_shifted_grid():
    r = ReplayInput.model_validate(shifted(8_400))
    ticks = recovery.ticks(r, 8_400)
    # 0 다음은 첫 기록 [400, 1400) 의 끝부터 — 1000 은 그 기록 안이라 부르지 않는다
    assert ticks == [0, 1_400, 2_400, 3_400, 4_400, 5_400, 6_400, 7_400]
    spans = [(g.t_ms, g.t_ms + g.duration_ms) for g in r.gaze_records]
    assert not [t for t in ticks if any(start < t < end for start, end in spans)]


def test_ticks_fill_a_gap_without_records_every_second():
    gaze = [{"t_ms": t, "duration_ms": 1_000, "state": "CAMERA"} for t in (0, 1_000, 2_000)]
    gaze += [
        {"t_ms": t, "duration_ms": 1_000, "state": "CAMERA"} for t in range(13_000, 16_000, 1_000)
    ]
    r = ReplayInput.model_validate(raw(16_000, gaze_records=gaze, voice_records=[]))
    # 3초 ~ 13초는 기록이 없어 1초 간격으로 채운다
    assert recovery.ticks(r, 16_000) == list(range(0, 16_000, 1_000))


def test_ticks_fill_before_the_first_record_and_skip_times_inside_a_record():
    gaze = [{"t_ms": 2_500, "duration_ms": 1_000, "state": "CAMERA"}]
    r = ReplayInput.model_validate(raw(6_000, gaze_records=gaze, voice_records=[]))
    # 1000 · 2000 은 채우고, 3000 은 기록 [2500, 3500) 안이라 건너뛴 뒤 끝 3500 부터 잇는다
    assert recovery.ticks(r, 6_000) == [0, 1_000, 2_000, 3_500, 4_500, 5_500]


def test_word_confirmed_at_the_request_time_goes_in_the_next_request():
    r = ReplayInput.model_validate(
        raw(
            10_000,
            words=[{"word": "가나다", "start_ms": 2_000, "end_ms": 2_600, "final_at_ms": 4_000}],
        )
    )
    assert recovery.inputs_at(r, 4_000).words == []
    assert [w.start_ms for w in recovery.inputs_at(r, 5_000).words] == [2_000]


def test_last_window_takes_words_confirmed_exactly_at_the_take_end():
    r = ReplayInput.model_validate(
        raw(
            10_000,
            words=[{"word": "가나다", "start_ms": 9_200, "end_ms": 9_800, "final_at_ms": 10_000}],
        )
    )
    assert recovery.inputs_at(r, 10_000).words == []
    assert [w.start_ms for w in recovery.inputs_at(r, 10_000, last_window=True).words] == [9_200]


def test_replay_of_a_shifted_grid_matches_the_live_run():
    t_end = 20_400
    data = shifted(t_end)
    # 실시간: BE 가 1초 기록이 끝날 때마다 요청을 보내고 coach_state 를 이어 간다
    live_req = request(t_ms=t_end, replay=data)
    parsed = ReplayInput.model_validate(data)
    judges = fake_judges()
    state = None
    for t in range(1_400, t_end, 1_000):
        creq = recovery.request_at(FinalizeRequest.model_validate(live_req), parsed, t, state)
        state = decide(creq, judges).coach_state
    live = finalize(
        {
            **live_req,
            "replay": None,
            "coach_state": state,
            "inputs": recovery.inputs_at(parsed, t_end, last_window=True).model_dump(mode="json"),
        },
        judges,
    ).take_result
    replayed = finalize(live_req, fake_judges()).take_result

    assert replayed.replayed is True and live.replayed is False
    for area, result in live.areas.items():
        assert replayed.areas[area].take == result.take, area
        assert replayed.areas[area].slides == result.slides, area


def test_rebuild_requests_are_exam_mode_every_second(monkeypatch: pytest.MonkeyPatch):
    seen: list[tuple[int, str]] = []
    real = core_mod.decide

    def spy(req, judges, config=None, policy=None):
        seen.append((req.t_ms, req.mode.value))
        return real(req, judges, config, policy)

    monkeypatch.setattr(core_mod, "decide", spy)
    finalize(request(t_ms=5_000), fake_judges())
    # Take 시작(0초)부터 1초마다, Take 끝(5초)은 finalize 의 마지막 창
    assert seen == [(t, "EXAM") for t in (0, 1_000, 2_000, 3_000, 4_000)]


def test_slide_change_inside_the_first_second_is_kept():
    slides = [{"number": 1, "started_ms": 0}, {"number": 2, "started_ms": 600}]
    r = finalize(request(replay=raw(10_000, slides=slides), t_ms=10_000), fake_judges())
    durations = {
        s.slide_number: s.metrics["slide_duration_ms"] for s in r.take_result.areas["TIME"].slides
    }
    assert durations == {1: 600, 2: 9_400}


def test_stt_before_the_first_known_status_is_not_trusted():
    none = ReplayInput.model_validate(raw(10_000, stt_status_changes=[]))
    late = ReplayInput.model_validate(
        raw(10_000, stt_status_changes=[{"t_ms": 3_000, "status": "ok"}])
    )
    assert recovery.inputs_at(none, 5_000).stt_status == "connecting"
    assert recovery.inputs_at(late, 2_000).stt_status == "connecting"
    assert recovery.inputs_at(late, 3_000).stt_status == "ok"


def test_internal_error_while_rebuilding_skips_that_second(monkeypatch: pytest.MonkeyPatch):
    real = core_mod.measure.build_tick

    def flaky(req, *args):
        if req.t_ms == 3_000:
            raise RuntimeError("가짜 예외")
        return real(req, *args)

    monkeypatch.setattr(core_mod.measure, "build_tick", flaky)
    r = finalize(request(t_ms=10_000), fake_judges()).take_result
    assert r.replayed is True and r.areas["TIME"].take["duration_ms"] == 10_000


# ── Take 결과 ───────────────────────────────────────────────────────────


def test_replay_rebuilds_the_take_without_coach_state():
    fin = finalize(request(), fake_judges())
    r = fin.take_result
    assert r.replayed is True
    # 다시 판정한 합계가 Take 를 덮는다 (시간 판정은 코치 안에 있어 실제 summarize 를 쓴다)
    assert r.areas["TIME"].take["duration_ms"] == 30_000
    assert [s.slide_number for s in r.areas["TIME"].slides] == [1, 2]
    assert fin.events == []  # 다시 판정한 이벤트는 내지 않는다


def test_without_replay_a_missing_state_still_needs_replay():
    req = request()
    req["replay"] = None
    with pytest.raises(ReplayRequired):
        finalize(req, fake_judges())


def test_interventions_and_gave_up_come_from_the_received_events():
    events = [
        intervention(8_000, "GAZE_ON_SCRIPT", "GAZE", "LOOK_AT_CAMERA", 1),
        {
            "kind": "STRATEGY",
            "event_id": "st-GAZE_ON_SCRIPT-1-20000",
            "t_ms": 20_000,
            "issue_type": "GAZE_ON_SCRIPT",
            "area": "GAZE",
            "slide_number": 1,
            "change": "GAVE_UP",
            "from_instruction": "LOOK_AT_CAMERA",
            "from_variant": "sentence_start",
            "intervention_id": "iv-8000",
        },
    ]
    r = finalize(request(events), fake_judges()).take_result
    assert [i.intervention_id for i in r.interventions] == ["iv-8000"]
    assert [(g.issue_type.value, g.slide_number) for g in r.gave_up] == [("GAZE_ON_SCRIPT", 1)]


def test_missing_outcomes_only_for_interventions_that_measure_one():
    events = [
        intervention(8_000, "GAZE_ON_SCRIPT", "GAZE", "LOOK_AT_CAMERA", 1),  # 효과가 없음 → 냄
        intervention(12_000, "PACE_FAST", "SPEED", "SLOW_DOWN", 1),  # 효과가 있음 → 안 냄
        {
            "kind": "OUTCOME",
            "event_id": "oc-iv-12000",
            "t_ms": 22_000,
            "intervention_id": "iv-12000",
            "outcome": "EFFECTIVE",
            "metric": "cpm_short",
            "before": 400.0,
            "after": 300.0,
        },
        intervention(25_000, "TIME_OVER", "TIME", "WRAP_UP", 2),  # 효과를 재지 않는 마무리
        intervention(27_000, "IMPROVED_AFTER_FEEDBACK", "GAZE", "CONTINUE", 2),  # 격려
    ]
    fin = finalize(request(events), fake_judges())
    assert [(e.kind, e.event_id, e.outcome.value) for e in fin.events] == [
        ("OUTCOME", "oc-iv-8000", "NOT_MEASURED")
    ]
    by_id = {i.intervention_id: i.outcome for i in fin.take_result.interventions}
    assert by_id["iv-8000"].value == "NOT_MEASURED" and by_id["iv-12000"].value == "EFFECTIVE"


def _scripted(area: str, issue: str, start: int, end: int) -> ScriptedJudge:
    module = {"GAZE": "gaze", "SPEED": "pace"}[area]
    return ScriptedJudge(
        module, lambda t: {"issues": [fake_issue(area, issue)] if start <= t <= end else []}
    )


def test_coached_comes_from_actual_interventions_overlapping_the_segment():
    judges = fake_judges(gaze=_scripted("GAZE", "GAZE_ON_SCRIPT", 3_000, 9_000))
    coached = finalize(
        request([intervention(6_000, "GAZE_ON_SCRIPT", "GAZE", "LOOK_AT_CAMERA", 1)]), judges
    ).take_result
    other_slide = finalize(
        request([intervention(6_000, "GAZE_ON_SCRIPT", "GAZE", "LOOK_AT_CAMERA", 2)]), judges
    ).take_result
    none = finalize(request(), judges).take_result
    seg = [s for s in coached.problem_segments if s.issue_type.value == "GAZE_ON_SCRIPT"]
    assert [s.coached for s in seg] == [True]
    # 장 단위 문제는 같은 장의 개입만 센다
    assert [s.coached for s in other_slide.problem_segments if s.area.value == "GAZE"] == [False]
    assert [s.coached for s in none.problem_segments if s.area.value == "GAZE"] == [False]


def test_take_level_issue_is_coached_even_if_the_slide_changed():
    judges = fake_judges(pace=_scripted("SPEED", "PACE_FAST", 10_000, 20_000))
    r = finalize(
        request([intervention(18_000, "PACE_FAST", "SPEED", "SLOW_DOWN", 2)]), judges
    ).take_result
    assert [s.coached for s in r.problem_segments if s.issue_type.value == "PACE_FAST"] == [True]


def test_short_episode_is_kept_only_when_an_actual_intervention_hit_it():
    # 1초만 보인 문제: 실제로 말을 걸었으면 남고, 아니면 짧은 잡음이라 빠진다
    judges = fake_judges(gaze=_scripted("GAZE", "GAZE_ON_SCRIPT", 6_000, 6_000))
    hit = finalize(
        request([intervention(6_000, "GAZE_ON_SCRIPT", "GAZE", "LOOK_AT_CAMERA", 1)]), judges
    )
    miss = finalize(request(), judges)

    def gaze(fin):
        return [s.coached for s in fin.take_result.problem_segments if s.area.value == "GAZE"]

    assert gaze(hit) == [True]
    assert gaze(miss) == []


def test_empty_raw_data_still_gives_a_result():
    empty = {
        "gaze_records": [],
        "voice_records": [],
        "stt_status_changes": [],
        "words": [],
        "utterance_ends": [],
        "slides": [],
    }
    r = finalize(request(replay=empty), fake_judges()).take_result
    assert r.replayed is True and r.duration_ms == 30_000


def test_same_replay_same_result():
    a = finalize(request(), fake_judges())
    b = finalize(request(), fake_judges())
    assert a == b
