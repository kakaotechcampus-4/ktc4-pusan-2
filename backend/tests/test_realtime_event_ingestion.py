"""FrameSequencer — 순서·중복·갭, 그리고 탭 새로고침(seq 를 처음부터 다시 셈) 판정.

AudioBudget — Deepgram 에 보낼 길이를 서버가 실제로 지난 시간으로 묶는다.
"""

from pitch_coach_backend.realtime.audio import BYTES_PER_MS
from pitch_coach_backend.realtime.event_ingestion import (
    AUDIO_BURST_MS,
    MAX_SILENCE_FILL_MS,
    AudioBudget,
    AudioFrame,
    FrameSequencer,
)


def frame(seq: int, offset_ms: int, duration_ms: int = 100) -> AudioFrame:
    return AudioFrame(seq=seq, offset_ms=offset_ms, pcm=b"\x01" * (duration_ms * BYTES_PER_MS))


def test_backward_seq_on_the_same_connection_is_dropped():
    sequencer = FrameSequencer()
    sequencer.new_connection()
    assert sequencer.accept(frame(1, 0)) is not None
    assert sequencer.accept(frame(2, 100)) is not None
    assert sequencer.accept(frame(2, 100)) is None
    assert sequencer.accept(frame(1, 0)) is None
    assert sequencer.dropped == 2


def test_first_frame_of_the_first_connection_is_not_a_restart():
    sequencer = FrameSequencer()
    sequencer.new_connection()
    accepted = sequencer.accept(frame(1, 0))
    assert accepted is not None and not accepted.restart


def test_reconnect_that_continues_seq_is_not_a_restart():
    sequencer = FrameSequencer()
    sequencer.new_connection()
    sequencer.accept(frame(1, 0))
    sequencer.new_connection()
    accepted = sequencer.accept(frame(2, 100))
    assert accepted is not None
    assert not accepted.restart and not accepted.timeline_break


def test_new_connection_starting_over_is_a_restart_and_breaks_the_timeline():
    sequencer = FrameSequencer()
    sequencer.new_connection()
    for i in range(30):
        sequencer.accept(frame(i + 1, i * 100))

    sequencer.new_connection()
    first = sequencer.accept(frame(1, 0))
    assert first is not None
    assert first.restart and first.timeline_break
    # 갭으로 계산하지 않는다 (offset 이 뒤로 갔다)
    assert (first.silence_ms, first.lost_ms) == (0, 0)

    # 그 뒤로는 새 기준으로 순서를 본다
    second = sequencer.accept(frame(2, 100))
    assert second is not None and not second.restart
    assert sequencer.accept(frame(2, 100)) is None


def test_reload_that_resumes_the_stage_clock_is_a_restart():
    """새로고침하면 seq 만 1 부터다. offset 은 이어받은 무대 시계라 앞으로 간다."""
    sequencer = FrameSequencer()
    sequencer.new_connection()
    for i in range(30):
        sequencer.accept(frame(i + 1, i * 100))

    sequencer.new_connection()
    first = sequencer.accept(frame(1, 4_000))
    assert first is not None and first.restart
    # 새로고침하는 동안의 공백을 무음으로 채우지 않는다. 세션을 갈아 base 를 다시 잡는다
    assert (first.silence_ms, first.lost_ms) == (0, 0)


def test_new_connection_with_forward_seq_but_backward_offset_is_a_restart():
    """이전 탭이 몇 프레임만 보냈으면 새 탭의 첫 seq 가 더 클 수 있다. offset 으로 가린다."""
    sequencer = FrameSequencer()
    sequencer.new_connection()
    for i in range(5):
        sequencer.accept(frame(i + 1, 3_000 + i * 100))

    sequencer.new_connection()
    first = sequencer.accept(frame(12, 1_500))
    assert first is not None and first.restart


def test_only_the_first_frame_of_a_connection_can_restart():
    sequencer = FrameSequencer()
    sequencer.new_connection()
    sequencer.accept(frame(1, 0))
    sequencer.accept(frame(2, 100))
    # 같은 연결에서 뒤로 간 프레임은 restart 가 아니라 역행이다
    assert sequencer.accept(frame(1, 0)) is None


# ── 보낼 길이를 서버 시간으로 묶기 ────────────────────────────────────


class Clock:
    """서버 시각(ms). 테스트가 직접 돌린다."""

    def __init__(self) -> None:
        self.now = 0.0
        self.budget = AudioBudget(self.now)

    def allow(self, cost_ms: int) -> bool:
        return self.budget.take(cost_ms, self.now)


def test_budget_refills_with_server_time_up_to_the_burst():
    budget = AudioBudget(0)

    assert budget.take(AUDIO_BURST_MS, 0)
    assert not budget.take(1, 0)
    assert budget.take(500, 500)
    # 오래 쉬어도 `AUDIO_BURST_MS` 까지만 모은다
    assert not budget.take(AUDIO_BURST_MS + 1, 60_000)
    assert budget.take(AUDIO_BURST_MS, 60_000)


def test_frames_sent_in_real_time_are_all_accepted():
    clock = Clock()
    sequencer = FrameSequencer()
    sequencer.new_connection()

    for i in range(600):
        clock.now = i * 100
        assert sequencer.accept(frame(i + 1, i * 100), allow=clock.allow) is not None

    assert sequencer.over_budget == 0


def test_tiny_frames_with_long_gaps_cannot_outrun_server_time():
    """1ms 프레임 하나가 5초 무음이 된다. 서버 시간이 가지 않으면 `AUDIO_BURST_MS` 만큼만 받는다."""
    clock = Clock()
    sequencer = FrameSequencer()
    sequencer.new_connection()
    sent_ms = 0

    for i in range(720):
        accepted = sequencer.accept(frame(i + 1, i * MAX_SILENCE_FILL_MS, 1), allow=clock.allow)
        if accepted is not None:
            sent_ms += accepted.silence_ms + accepted.frame.duration_ms

    assert sent_ms <= AUDIO_BURST_MS
    assert sequencer.over_budget > 700
    assert sequencer.dropped == sequencer.over_budget
    # 버린 뒤에도 세션을 갈지 않는다. 버릴 때마다 Deepgram 연결이 하나씩 생기면 안 된다
    assert sequencer.lost_ms == 719 * MAX_SILENCE_FILL_MS + 1 - sent_ms


def test_reconnect_flush_with_a_short_gap_fits_in_the_burst():
    """4초 끊겼다 다시 붙으면 FE 가 쌓아 둔 2초와 그 앞 2초 틈(무음)을 한 번에 보낸다."""
    clock = Clock()
    sequencer = FrameSequencer()
    sequencer.new_connection()
    for i in range(10):
        clock.now = i * 100
        assert sequencer.accept(frame(i + 1, i * 100), allow=clock.allow) is not None

    clock.now = 5_000
    sequencer.new_connection()
    flushed = [
        sequencer.accept(frame(31 + i, 3_000 + i * 100), allow=clock.allow) for i in range(20)
    ]

    assert all(a is not None for a in flushed)
    assert flushed[0] is not None and flushed[0].silence_ms == 2_000
    assert not flushed[0].restart


def test_frame_over_the_budget_is_dropped_and_counted_as_lost():
    """버린 구간을 다음 프레임 앞에 무음으로 메우지 않는다. 메우면 버린 만큼 다시 보낸다."""
    clock = Clock()
    clock.budget = AudioBudget(0, burst_ms=300)
    sequencer = FrameSequencer()
    sequencer.new_connection()
    assert sequencer.accept(frame(1, 0), allow=clock.allow) is not None
    assert sequencer.accept(frame(2, 100), allow=clock.allow) is not None
    assert sequencer.accept(frame(3, 200), allow=clock.allow) is not None
    assert sequencer.accept(frame(4, 300), allow=clock.allow) is None

    clock.now = 1_000
    accepted = sequencer.accept(frame(5, 400), allow=clock.allow)

    assert accepted is not None
    assert (accepted.silence_ms, accepted.lost_ms) == (0, 0)
    assert (sequencer.frames, sequencer.dropped, sequencer.over_budget) == (4, 1, 1)
    assert sequencer.lost_ms == 100


def test_restart_frame_over_the_budget_leaves_the_next_frame_to_decide_the_restart():
    clock = Clock()
    clock.budget = AudioBudget(0, burst_ms=200)
    sequencer = FrameSequencer()
    sequencer.new_connection()
    assert sequencer.accept(frame(1, 0), allow=clock.allow) is not None
    assert sequencer.accept(frame(2, 100), allow=clock.allow) is not None

    # 탭을 새로고침했다. 첫 프레임은 길이가 모자라 버린다
    sequencer.new_connection()
    assert sequencer.accept(frame(1, 50), allow=clock.allow) is None
    clock.now = 1_000
    accepted = sequencer.accept(frame(2, 150), allow=clock.allow)

    assert accepted is not None and accepted.restart
    assert accepted.silence_ms == 0


def test_long_gap_costs_only_the_frame_because_it_is_not_filled():
    clock = Clock()
    clock.budget = AudioBudget(0, burst_ms=200)
    sequencer = FrameSequencer()
    sequencer.new_connection()
    assert sequencer.accept(frame(1, 0), allow=clock.allow) is not None

    accepted = sequencer.accept(frame(2, 60_000), allow=clock.allow)

    assert accepted is not None and accepted.lost_ms > MAX_SILENCE_FILL_MS
