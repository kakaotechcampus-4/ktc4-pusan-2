"""FrameSequencer — 순서·중복·갭, 그리고 탭 새로고침(seq 를 처음부터 다시 셈) 판정."""

from pitch_coach_backend.realtime.audio import BYTES_PER_MS
from pitch_coach_backend.realtime.event_ingestion import AudioFrame, FrameSequencer


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
