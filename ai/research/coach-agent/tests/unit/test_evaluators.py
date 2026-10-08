"""평가기 — 측정값이 문제로 바뀌는 계산."""

from __future__ import annotations

import pytest

from coach import decide
from coach.evaluators.speech import compute_cpm
from coach.renderer import format_duration
from coach.schemas import Word
from coach.state import CoachState, dump_state

from .conftest import IN_PAUSE, Session, gaze_script, make_request, words


def _cands(resp):
    return {c.issue_type.value: c for c in resp.candidates}


# ── 말 속도 ───────────────────────────────────────────────────────────────


def test_cpm_matches_stt_live_formula():
    # 글자 수 ÷ 실제로 말한 시간(침묵 제외) × 60초 — stt-live v1 과 같다
    ws = [Word(w="가나다", start_ms=i * 1000, end_ms=i * 1000 + 600) for i in range(10)]
    cpm, speak_ms, n = compute_cpm(ws, 10_000, 15_000)
    assert speak_ms == 6000 and n == 10
    assert cpm == pytest.approx(30 / 6 * 60)  # 침묵 4초를 빼서 300


def test_cpm_ignores_fillers():
    ws = [
        Word(w="가나다", start_ms=0, end_ms=600),
        Word(w="음", start_ms=600, end_ms=900, filler=True),
    ]
    cpm, speak_ms, _ = compute_cpm(ws, 1000, 15_000)
    assert speak_ms == 600 and cpm == pytest.approx(300)


def test_pace_fast_is_detected_above_350():
    resp = decide(make_request(20_000, speech={"words": words(20_000, cpm=420)}))
    c = _cands(resp)["PACE_FAST"]
    assert c.area.value == "SPEED" and c.instruction.value == "SLOW_DOWN"
    assert resp.indicators.pace.value == "FAST"


def test_pace_needs_enough_speech():
    resp = decide(make_request(20_000, speech={"words": words(20_000, cpm=420, seconds=2)}))
    assert "PACE_FAST" not in _cands(resp)
    assert resp.indicators.cpm is None


def test_degraded_stt_keeps_pace_out():
    resp = decide(
        make_request(20_000, speech={"stt_status": "degraded", "words": words(20_000, cpm=420)})
    )
    c = _cands(resp)["PACE_FAST"]
    assert c.status.value == "IGNORED" and "SENSOR_UNUSABLE" in c.reasons
    assert resp.indicators.pace.value == "UNKNOWN"


# ── 시선 ─────────────────────────────────────────────────────────────────


def test_gaze_script_ratio_counts_only_visible_time():
    # 보인 시간의 85% 를 대본에 — UNCERTAIN 이 30% 여도 대본 응시로 잡는다
    resp = decide(make_request(20_000, gaze=gaze_script(0.85, uncertain=0.3)))
    c = _cands(resp)["GAZE_ON_SCRIPT"]
    assert c.status.value == "WAITING"  # 지속 3초 미달
    assert resp.indicators.gaze.value == "SCRIPT"


def test_gaze_with_mostly_uncertain_frames_is_not_trusted():
    resp = decide(make_request(20_000, gaze=gaze_script(0.85, uncertain=0.6)))
    c = _cands(resp)["GAZE_ON_SCRIPT"]
    assert c.status.value == "IGNORED" and "SENSOR_UNUSABLE" in c.reasons
    assert resp.indicators.gaze.value == "UNCERTAIN"


def test_long_continuous_script_gaze_triggers_even_with_moderate_ratio():
    resp = decide(make_request(20_000, gaze=gaze_script(0.62, streak_ms=6000)))
    assert "GAZE_ON_SCRIPT" in _cands(resp)


def test_streak_alone_with_low_ratio_is_noise():
    # 라벨이 흔들리면 5초 연속이 우연히 생긴다 — 창 비율이 0.6 미만이면 연속 응시로 잡지 않는다
    resp = decide(make_request(20_000, gaze=gaze_script(0.5, streak_ms=6000)))
    assert "GAZE_ON_SCRIPT" not in _cands(resp)


# ── 음량 · 침묵 ───────────────────────────────────────────────────────────


def test_volume_uses_recent_average_while_speaking():
    s = Session()
    for t in range(1000, 8001, 1000):
        s.step(t, voice={"relative_db": -9.0, "silence_ms": 0, "audio_live": True})
    c = _cands(s.responses[-1])["VOLUME_LOW"]
    assert c.area.value == "VOLUME"
    assert s.responses[-1].indicators.volume.value == "LOW"


def test_silence_while_audio_dead_is_not_a_pause_problem():
    resp = decide(
        make_request(20_000, voice={"relative_db": None, "silence_ms": 9000, "audio_live": False})
    )
    c = _cands(resp)["LONG_SILENCE"]
    assert c.status.value == "IGNORED" and "SENSOR_UNUSABLE" in c.reasons


def test_silence_counts_only_after_audio_comes_back():
    s = Session()
    s.step(10_000, voice={"relative_db": None, "silence_ms": 3000, "audio_live": False})
    back = s.step(11_000, voice={"relative_db": None, "silence_ms": 9000, "audio_live": True})
    assert "LONG_SILENCE" not in _cands(back)  # FE 가 넘긴 9초 중 8초는 오디오가 죽어 있던 시간
    later = s.step(17_000, voice={"relative_db": None, "silence_ms": 15_000, "audio_live": True})
    assert "LONG_SILENCE" in _cands(later)


# ── 시간 ─────────────────────────────────────────────────────────────────


def _state_with_chars(slide: int, chars: int) -> dict:
    """이 장에서 chars 자를 말한 상태. 진행도를 글자 수로 재려면 요청에 speech 도 있어야 한다."""
    st = CoachState(slide_number=slide, slide_chars={str(slide): chars}, last_final_end_ms=10**9)
    return dump_state(st)


def test_required_ratio_matches_the_worked_example():
    # 1분 55초, 3번 장의 1/4(150/600자). 남은 내용 45 + 30 = 75초, 남은 시간 65초 → r = 1.15
    resp = decide(
        make_request(
            115_000,
            slide=3,
            slide_elapsed=35_000,
            state=_state_with_chars(3, 150),
            speech={"words": words(115_000, cpm=370)},
        )
    )
    assert resp.indicators.required_ratio == pytest.approx(75 / 65, abs=1e-3)
    assert resp.indicators.schedule.value == "BEHIND"
    c = _cands(resp)["BEHIND_SCHEDULE"]
    # 370 × 1.15 = 427 > 350 → 속도로는 못 따라잡는다 → 내용을 줄인다
    assert c.instruction.value == "CONDENSE"


def test_behind_but_catchable_says_speed_up():
    resp = decide(
        make_request(
            115_000,
            slide=3,
            slide_elapsed=35_000,
            state=_state_with_chars(3, 150),
            speech={"words": words(115_000, cpm=280)},
        )
    )
    assert _cands(resp)["BEHIND_SCHEDULE"].instruction.value == "SPEED_UP"
    assert resp.action.value == "INTERVENE"
    assert resp.feedback.message == "조금만 빠르게 — 남은 2장, 1분 5초"


def test_slow_down_is_suppressed_while_behind():
    resp = decide(
        make_request(
            115_000,
            slide=3,
            slide_elapsed=35_000,
            state=_state_with_chars(3, 150),
            speech={"words": words(115_000, cpm=420)},
        )
    )
    assert "TIME_PRESSURE" in _cands(resp)["PACE_FAST"].reasons


def test_on_plan_final_minute_says_nothing():
    # 계획상 2분 10초에 3번 장 끝무렵이면 정상 — '결론으로'를 말하지 않는다
    resp = decide(
        make_request(
            130_000,
            slide=3,
            slide_elapsed=40_000,
            state=_state_with_chars(3, 450),
            speech={"words": []},
        )
    )
    assert "FINAL_MINUTE" not in _cands(resp)
    assert resp.indicators.schedule.value == "ON_TRACK"


def test_final_minute_without_slide_plan_falls_back_to_remaining_time():
    resp = decide(make_request(125_000, plan={"target_ms": 180_000}, slide=None))
    assert _cands(resp)["FINAL_MINUTE"].instruction.value == "WRAP_UP"


def test_time_over_once():
    s = Session(plan={"target_ms": 60_000}, slide=None)
    s.run(61_000, 200_000)
    wraps = [r for r in s.interventions if r.feedback.instruction.value == "WRAP_UP"]
    assert len(wraps) == 1
    assert wraps[0].feedback.message == "제한 시간을 넘겼어요 — 한 문장으로 마무리하세요"


def test_slide_over_says_move_on_but_not_on_last_slide():
    resp = decide(
        make_request(
            100_000,
            slide=2,
            slide_elapsed=95_000,
            state=_state_with_chars(2, 500),
            speech={"words": []},
        )
    )
    c = _cands(resp)["SLIDE_OVER"]
    assert c.instruction.value == "MOVE_ON"
    last = decide(
        make_request(170_000, slide=4, slide_elapsed=60_000, state=_state_with_chars(4, 200))
    )
    assert "SLIDE_OVER" not in _cands(last)


def test_ahead_uses_projected_end_not_ratio():
    # 2분 10초에 3번 장 82% — 계획보다 9초쯤 앞섬. r 은 0.82 지만 예상 종료(약 168초)는 허용 범위 안
    resp = decide(
        make_request(
            130_000,
            slide=3,
            slide_elapsed=46_000,
            state=_state_with_chars(3, 492),
            speech={"words": []},
        )
    )
    assert resp.indicators.required_ratio < 0.85
    assert "AHEAD_OF_SCHEDULE" not in _cands(resp)
    # 1분에 이미 3번 장 절반 — 이대로면 2분 안에 끝난다
    fast = decide(
        make_request(
            60_000,
            slide=3,
            slide_elapsed=10_000,
            state=_state_with_chars(3, 300),
            speech={"words": []},
        )
    )
    assert "AHEAD_OF_SCHEDULE" in _cands(fast)


def test_unheard_slide_measures_progress_by_time():
    s = Session(slide=2)
    s.step(
        40_000,
        slide_elapsed=10_000,
        voice={"relative_db": None, "silence_ms": 0, "audio_live": False},
    )
    resp = s.step(41_000, slide_elapsed=11_000, voice=IN_PAUSE, speech={"words": []})
    # 2번 장은 STT 가 못 들은 구간이 있어 말한 글자 0자 대신 시간(11/60)으로 진행도를 잰다
    assert resp.indicators.schedule.value != "BEHIND"


def test_format_duration():
    assert format_duration(65_000) == "1분 5초"
    assert format_duration(50_000) == "50초"
    assert format_duration(120_000) == "2분"
    assert format_duration(-5) == "0초"
