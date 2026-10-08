"""측정 — 리뷰 근거 실험(coach_lab.evaluate)에서 찾아 고친 것들의 회귀 테스트."""

from __future__ import annotations

from coach import decide
from coach.config import load_config

from .conftest import IN_PAUSE, Session, gaze_script, make_request, words


def _cand(resp, issue):
    return next((c for c in resp.candidates if c.issue_type.value == issue), None)


def test_persistence_counts_only_reliable_time():
    """센서가 나쁜 동안은 지속시간을 세지 않는다 — 좋아진 뒤 3초를 다시 채워야 말한다."""
    s = Session(plan={})
    s.run(10_000, 20_000, gaze=gaze_script(0.9, uncertain=0.7))
    out = s.run(21_000, 45_000, gaze=gaze_script(0.9))
    first = next(r.t_ms for r in out if r.feedback is not None)
    assert first >= 21_000 + 3_000


def test_one_good_window_after_a_bad_stretch_is_not_trusted():
    s = Session(plan={})
    s.run(10_000, 20_000, gaze=gaze_script(0.9, uncertain=0.7))
    resp = s.step(21_000, gaze=gaze_script(0.9, uncertain=0.3))
    assert "SENSOR_UNUSABLE" in _cand(resp, "GAZE_ON_SCRIPT").reasons


def test_streak_needs_a_minimum_ratio():
    cfg = load_config()
    assert cfg.gaze.streak_min_ratio == 0.6
    resp = decide(make_request(20_000, plan={}, gaze=gaze_script(0.55, streak_ms=8_000)))
    assert _cand(resp, "GAZE_ON_SCRIPT") is None


def test_volume_needs_enough_speaking_samples():
    s = Session(plan={})
    quiet = {"relative_db": -10.0, "silence_ms": 0, "audio_live": True}
    first = s.step(10_000, voice=quiet)
    second = s.step(11_000, voice=quiet)
    third = s.step(12_000, voice=quiet)
    assert _cand(first, "VOLUME_LOW") is None and _cand(second, "VOLUME_LOW") is None
    assert _cand(third, "VOLUME_LOW") is not None


def test_speed_restarts_after_stt_recovers():
    """STT 가 불량이던 동안의 단어로 '빠르다'고 하지 않는다."""
    s = Session(plan={})
    for t in range(10_000, 20_001, 1_000):
        s.step(t, speech={"stt_status": "degraded", "words": words(t, cpm=420)})
    back = s.step(21_000, speech={"words": words(21_000, cpm=420)})
    assert back.indicators.cpm is None  # 돌아온 지 1초 — 잴 단어가 모자라다
    later = s.step(30_000, speech={"words": words(30_000, cpm=420)})
    assert later.indicators.cpm is not None  # 돌아온 뒤 9초치로는 잰다


def test_missing_speech_input_is_not_a_recovery():
    """STT 입력이 아예 없다가 생긴 것은 불량에서 돌아온 것이 아니다 — 15초 창 그대로 쓴다."""
    s = Session(plan={})
    s.step(10_000)
    resp = s.step(20_000, speech={"words": words(20_000, cpm=420)})
    assert resp.indicators.cpm is not None


def test_late_final_word_belongs_to_the_slide_it_was_said_on():
    s = Session(plan={})
    s.step(10_000, slide=3, slide_elapsed=10_000, speech={"words": []})
    s.step(11_000, slide=4, slide_elapsed=500, speech={"words": []})
    s.step(12_000, slide=4, slide_elapsed=1_500, speech={"words": []})
    # 3번 장에서 10.2초에 말한 단어의 확정이 4번 장으로 넘어간 뒤 13초에야 도착
    late = [{"w": "가나다라", "start_ms": 10_200, "end_ms": 10_500, "final": True}]
    resp = s.step(13_000, slide=4, slide_elapsed=2_500, speech={"words": late})
    assert resp.coach_state["slide_chars"].get("3") == 4


def test_silence_outcome_sees_speech_even_without_speaking_samples():
    cfg = load_config(policy={"pause_wait_max_ms": 0})
    s = Session(cfg, plan={})
    for t in range(10_000, 17_001, 1_000):
        s.step(t, voice={"relative_db": None, "silence_ms": t - 10_000, "audio_live": True})
    assert any(r.feedback and r.feedback.instruction.value == "RESUME" for r in s.responses)
    # 다시 말했지만 매 틱 침묵이 300ms 이상으로 잡힌다 (단어 사이 틈) — 그래도 침묵이 다시 시작됐다
    for t in range(18_000, 25_001, 1_000):
        s.step(t, voice={"relative_db": None, "silence_ms": 600, "audio_live": True})
    outcome = next(e for e in s.events if e.kind == "OUTCOME")
    assert outcome.outcome.value == "EFFECTIVE"


def test_speed_outcome_uses_the_recent_window():
    """15초 창에는 개입 전의 빠른 단어가 남는다. 효과는 최근 6초로 잰다."""
    s = Session(plan={})
    out = s.run(10_000, 26_000, voice=IN_PAUSE, speech=None)
    del out
    for t in range(27_000, 40_001, 1_000):
        s.step(t, speech={"words": words(t, cpm=420)})
    fired = next(r for r in s.responses if r.feedback is not None)
    t0 = fired.t_ms
    for t in range(t0 + 1_000, t0 + 12_001, 1_000):
        slow_since = t0 + 2_000
        ws = [w for w in words(t, cpm=420) if w["end_ms"] <= slow_since]
        ws += words(t, cpm=300, seconds=(t - slow_since) / 1000) if t > slow_since else []
        s.step(t, speech={"words": ws})
    outcome = next(e for e in s.events if e.kind == "OUTCOME")
    assert outcome.metric == "cpm_short"
    assert outcome.outcome.value == "EFFECTIVE"
