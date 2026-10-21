"""말 없음 판단과 자동 종료 조건. 네트워크 없이 숫자만 확인한다.

스트림에 붙인 흐름(알림·정리·재연결 거절)은 test_realtime_ws.py 의 "자동 종료" 절에 있다.
"""

import pytest

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.realtime.auto_end import AutoEndPolicy, SilenceTracker
from pitch_coach_backend.realtime.dto import AutoEndReason

# 2분 말이 없고, 직전 5분 중 80% 이상이 말 없음
POLICY = AutoEndPolicy(
    silence_end_ms=120_000,
    silence_window_ms=300_000,
    silence_ratio=0.8,
    max_duration_ms=3_600_000,
)


def feed_silence(tracker: SilenceTracker, start_ms: int, end_ms: int, step_ms: int = 5_000):
    """말 없는 final 을 step 간격으로 넣는다. 마지막 결과를 돌려준다."""
    result = False
    for t in range(start_ms, end_ms, step_ms):
        result = tracker.observe(t, t + step_ms, [], has_text=False)
    return result


def feed_speech(tracker: SilenceTracker, start_ms: int, end_ms: int, step_ms: int = 5_000):
    """구간 전체가 말인 final 을 넣는다."""
    for t in range(start_ms, end_ms, step_ms):
        assert tracker.observe(t, t + step_ms, [(t, t + step_ms)], has_text=True) is False


def test_silence_from_the_start_ends_after_silence_end():
    """말이 한 번도 없으면 창이 덜 찼어도 확인된 구간 전부가 말 없음이다."""
    tracker = SilenceTracker(POLICY)
    assert feed_silence(tracker, 0, 115_000) is False
    assert tracker.observe(115_000, 120_000, [], has_text=False) is True


def test_after_active_speech_the_window_ratio_also_has_to_be_met():
    """3분 내내 말하다 멈추면 2분 말 없음만으로는 안 끝난다 — 창 5분 중 말 없음이 40% 뿐이다.
    4분이 지나 80% 가 되면 끝난다."""
    tracker = SilenceTracker(POLICY)
    feed_speech(tracker, 0, 180_000)
    assert feed_silence(tracker, 180_000, 300_000) is False
    assert tracker.silent_run_ms == 120_000
    assert feed_silence(tracker, 300_000, 415_000) is False
    assert tracker.observe(415_000, 420_000, [], has_text=False) is True


def test_one_word_restarts_the_silent_run():
    tracker = SilenceTracker(POLICY)
    feed_silence(tracker, 0, 110_000)
    # 마지막 말이 111초에 끝났다
    assert tracker.observe(110_000, 115_000, [(110_500, 111_000)], has_text=True) is False
    assert tracker.silent_run_ms == 4_000
    assert feed_silence(tracker, 115_000, 230_000) is False
    assert tracker.observe(230_000, 235_000, [], has_text=False) is True


def test_time_without_transcripts_is_not_silence():
    """final 이 안 온 구간(STT 장애·오디오 유실)은 축에서 빠진다. 사이가 10분 떠도 2분이 안 된다."""
    tracker = SilenceTracker(POLICY)
    assert tracker.observe(0, 60_000, [], has_text=False) is False
    assert tracker.observe(660_000, 719_000, [], has_text=False) is False
    assert tracker.silent_run_ms == 119_000


def test_text_without_word_timings_counts_as_speech():
    """말이 있었는지 확실하지 않으면 말한 것으로 본다. 잘못 끝내는 쪽이 더 나쁘다."""
    tracker = SilenceTracker(POLICY)
    feed_silence(tracker, 0, 115_000)
    assert tracker.observe(115_000, 120_000, [], has_text=True) is False
    assert tracker.silent_run_ms == 0


def test_words_are_clipped_to_the_final_and_overlaps_are_not_double_counted():
    tracker = SilenceTracker(
        AutoEndPolicy(
            silence_end_ms=1_000, silence_window_ms=10_000, silence_ratio=0.5, max_duration_ms=1
        )
    )
    # 구간 밖으로 나간 단어(-500~200)는 0~200 만, 겹친 단어(100~400, 300~500)는 100~500 으로
    tracker.observe(0, 1_000, [(-500, 200), (100, 400), (300, 500), (900, 900)], has_text=True)
    assert tracker.silent_run_ms == 500
    # 말 500ms / 확인 1000ms — 다음 1000ms 말 없음이면 말 없음 1500/2000 = 75%
    assert tracker.observe(1_000, 2_000, [], has_text=False) is True


def test_silence_filled_by_the_server_is_not_heard():
    """BE 가 메워 보낸 무음은 들은 게 아니다. 축에서 빠지고 말 없음 연속을 끊지도 늘리지도 않는다."""
    tracker = SilenceTracker(POLICY)
    assert feed_silence(tracker, 0, 100_000) is False
    # 5초 final 중 4초가 메운 무음이다. 들은 말 없음은 1초만 더해진다
    assert (
        tracker.observe(100_000, 105_000, [], has_text=False, unheard=[(101_000, 105_000)]) is False
    )
    assert tracker.silent_run_ms == 101_000
    # 전부 메운 무음인 final 은 아무것도 바꾸지 않는다
    assert (
        tracker.observe(105_000, 110_000, [], has_text=False, unheard=[(104_000, 111_000)]) is False
    )
    assert tracker.silent_run_ms == 101_000


def test_filled_silence_after_the_last_word_does_not_count():
    tracker = SilenceTracker(POLICY)
    tracker.observe(0, 10_000, [(0, 1_000)], has_text=True, unheard=[(2_000, 6_000)])
    # 마지막 말(1초) 뒤 9초 중 메운 4초를 뺀 5초
    assert tracker.silent_run_ms == 5_000


def test_empty_or_backward_final_is_ignored():
    tracker = SilenceTracker(POLICY)
    assert tracker.observe(5_000, 5_000, [], has_text=False) is False
    assert tracker.observe(5_000, 4_000, [], has_text=False) is False
    assert tracker.silent_run_ms == 0


def test_window_drops_old_audio():
    """창 밖으로 밀려난 말은 비율에서 빠진다. 창이 무한히 커지지 않는다."""
    tracker = SilenceTracker(POLICY)
    feed_speech(tracker, 0, 600_000)
    feed_silence(tracker, 600_000, 900_000)
    assert len(tracker._chunks) == 300_000 // 5_000
    assert tracker.should_end() is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"silence_end_ms": 0},
        {"max_duration_ms": -1},
        {"silence_ratio": 0},
        {"silence_ratio": 1.1},
        {"silence_end_ms": 400_000},  # 창(5분)보다 길다
        {"server_clock_slack_ms": -1},
    ],
)
def test_policy_rejects_values_that_never_or_always_fire(kwargs):
    base = {
        "silence_end_ms": 120_000,
        "silence_window_ms": 300_000,
        "silence_ratio": 0.8,
        "max_duration_ms": 3_600_000,
    }
    with pytest.raises(ValueError):
        AutoEndPolicy(**(base | kwargs))


def test_bad_settings_fail_when_the_policy_is_built(monkeypatch: pytest.MonkeyPatch):
    """설정 값 검사는 AutoEndPolicy 한 곳이다. 앱 시작 때 한 번 만들어 보므로 거기서 실패한다."""
    monkeypatch.setattr(settings, "take_silence_end_sec", 400)
    monkeypatch.setattr(settings, "take_silence_window_sec", 300)
    with pytest.raises(ValueError):
        AutoEndPolicy.from_settings()


def test_default_settings_build_a_valid_policy():
    policy = AutoEndPolicy.from_settings()
    assert policy == AutoEndPolicy(
        silence_end_ms=120_000,
        silence_window_ms=300_000,
        silence_ratio=0.8,
        max_duration_ms=3_600_000,
        server_clock_slack_ms=60_000,
    )


def test_messages_name_the_configured_time():
    assert POLICY.message(AutoEndReason.SILENCE) == "2분 넘게 말이 없어 연습을 자동으로 끝냈어요."
    assert POLICY.message(AutoEndReason.TIME_LIMIT) == "연습은 최대 1시간까지라 자동으로 끝냈어요."
    odd = AutoEndPolicy(
        silence_end_ms=90_000, silence_window_ms=90_000, silence_ratio=1, max_duration_ms=90_000
    )
    assert odd.message(AutoEndReason.SILENCE).startswith("90초 넘게")
