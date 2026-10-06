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


def test_only_the_first_frame_of_a_connection_can_restart():
    sequencer = FrameSequencer()
    sequencer.new_connection()
    sequencer.accept(frame(1, 0))
    sequencer.accept(frame(2, 100))
    # 같은 연결에서 뒤로 간 프레임은 restart 가 아니라 역행이다
    assert sequencer.accept(frame(1, 0)) is None
