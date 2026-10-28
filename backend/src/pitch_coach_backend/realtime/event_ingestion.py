"""FE -> BE 오디오 프레임의 검증·정규화·중복 제거. 네트워크도 Deepgram 도 모른다.

프레임 형식 (binary frame 하나):

    [seq: uint32 LE][offset_ms: uint32 LE][PCM: 16-bit signed LE, 16 kHz, mono]

offset_ms 는 Take 시작(RUNNING 이 된 시각) 기준이고 FE 가 performance.now() 로 잰다.
BE 는 이 값으로 (1) 순서 꼬임·중복을 버리고 (2) 끊긴 구간을 알아내 무음으로 채운다.

왜 채우는가: Deepgram 타임스탬프는 "그 연결에 보낸 첫 바이트 = 0" 이다. 중간에 빠진
오디오가 있으면 그 뒤의 모든 타임스탬프가 빠진 만큼 앞당겨진다. 갭을 무음으로 채우면
한 연결 안에서는 Deepgram 타임라인과 Take 타임라인이 같다. 무음도 과금되지만 몇 초 수준이다.

틈은 FE 가 보낸 offset_ms 로만 잰다. 그래서 Deepgram 에 보낼 길이(받은 PCM + 메울 무음)를
서버가 실제로 지난 시간으로 묶는다 (`AudioBudget`). 묶지 않으면 1ms 프레임 하나가 5초 무음이 되어
몇 초 만에 몇 시간 분량을 Deepgram 에 보낼 수 있다.
"""

import struct
from collections.abc import Callable
from dataclasses import dataclass

from pitch_coach_backend.realtime.audio import BYTES_PER_MS

FRAME_HEADER = struct.Struct("<II")

# 1초 넘는 프레임은 재연결 flush 가 아니라 오용으로 본다 (FE 버퍼는 1~2초지만 프레임 단위로 보낸다)
MAX_FRAME_MS = 1_000
MAX_FRAME_BYTES = FRAME_HEADER.size + MAX_FRAME_MS * BYTES_PER_MS

# performance.now() 지터. 이 이하로 어긋난 건 갭이 아니라 타이머 흔들림이다
GAP_TOLERANCE_MS = 50

# 이보다 큰 갭은 무음으로 채우지 않는다. 몇 분짜리 무음을 밀어 넣으면 그동안 실시간 전사가
# 멈추고 그만큼 과금된다. 대신 timeline_break 로 표시해 Deepgram 세션을 갈게 한다 —
# 부분만 채우면 그 뒤 타임스탬프가 못 채운 만큼 통째로 앞당겨진다.
MAX_SILENCE_FILL_MS = 5_000

# 서버 시간보다 앞서 보낼 수 있는 길이. 다시 붙을 때 FE 가 쌓아 둔 오디오(2초, FE
# `MAX_PENDING_FRAMES`)와 그 앞 틈을 메울 무음(최대 `MAX_SILENCE_FILL_MS`)을 한 번에 받을 만큼이다
AUDIO_BURST_MS = 10_000


class InvalidAudioFrame(ValueError):
    pass


@dataclass(frozen=True)
class AudioFrame:
    seq: int
    offset_ms: int
    pcm: bytes

    @property
    def duration_ms(self) -> int:
        return len(self.pcm) // BYTES_PER_MS

    @property
    def end_ms(self) -> int:
        return self.offset_ms + self.duration_ms


def parse_audio_frame(data: bytes) -> AudioFrame:
    if len(data) < FRAME_HEADER.size + 2:
        raise InvalidAudioFrame("프레임이 너무 짧습니다.")
    if len(data) > MAX_FRAME_BYTES:
        raise InvalidAudioFrame("프레임이 너무 큽니다.")
    seq, offset_ms = FRAME_HEADER.unpack_from(data)
    pcm = data[FRAME_HEADER.size :]
    if len(pcm) % 2:
        # Int16 정렬이 깨진 채 Deepgram 에 보내면 에러 없이 쓰레기 전사가 나온다
        raise InvalidAudioFrame("PCM 길이가 홀수입니다.")
    return AudioFrame(seq=seq, offset_ms=offset_ms, pcm=bytes(pcm))


@dataclass(frozen=True)
class Accepted:
    frame: AudioFrame
    # 프레임 앞에 먼저 보낼 무음 길이. 0 이면 이전 프레임과 이어진다
    silence_ms: int
    # 무음으로 메우기에는 너무 긴 갭이라 받지 못한 길이
    lost_ms: int
    # FE 가 처음부터 다시 센 새 연결의 첫 프레임 (탭 새로고침)
    restart: bool = False

    @property
    def timeline_break(self) -> bool:
        """이 프레임부터는 지금 Deepgram 세션의 타임라인에 이어 붙일 수 없다.

        갭을 무음으로 메우지 않았거나(lost_ms) FE 가 처음부터 다시 셌다(restart).
        그대로 보내면 이후 전사 시각이 어긋난다. 보내는 쪽(stream/runtime)이 세션을 갈아
        base 를 다시 잡아야 한다.
        """
        return self.lost_ms > 0 or self.restart


class AudioBudget:
    """Deepgram 에 보낼 길이를 서버가 실제로 지난 시간으로 묶는다 (토큰 버킷).

    지난 시간만큼 쌓이고 `AUDIO_BURST_MS` 까지 모아 둔다. FE 는 실시간으로 보내므로 평소에는 쌓이는
    만큼 쓰고, 다시 붙을 때 몰아 보내는 것은 끊긴 동안 쌓인 몫으로 받는다. 시각은 밖에서 받는다
    (monotonic, ms).
    """

    def __init__(self, now_ms: float, *, burst_ms: int = AUDIO_BURST_MS) -> None:
        self._burst_ms = burst_ms
        self._tokens = float(burst_ms)
        self._at = now_ms

    def take(self, cost_ms: int, now_ms: float) -> bool:
        """`cost_ms` 를 쓸 수 있으면 쓰고 True. 모자라면 쓰지 않고 False."""
        self._tokens = min(self._burst_ms, self._tokens + max(0.0, now_ms - self._at))
        self._at = now_ms
        if cost_ms > self._tokens:
            return False
        self._tokens -= cost_ms
        return True


class FrameSequencer:
    """프레임 순서를 추적하고 중복·역행을 버린다. 통계는 stt_status 로 FE 에 보낸다."""

    def __init__(self) -> None:
        self.frames = 0
        self.dropped = 0
        self.silence_ms = 0
        self.lost_ms = 0
        # 보낼 길이가 서버 시간을 앞질러 버린 프레임 (`accept` 의 `allow`). `dropped` 에도 센다
        self.over_budget = 0
        self._last: AudioFrame | None = None
        self._new_connection = False

    def new_connection(self) -> None:
        """연결이 바뀌었다. 그 연결의 첫 프레임으로 FE 가 처음부터 다시 셌는지 가린다."""
        self._new_connection = True

    def accept(
        self, frame: AudioFrame, *, allow: Callable[[int], bool] | None = None
    ) -> Accepted | None:
        """받아들이면 Accepted, 버리면 None.

        `allow` 는 Deepgram 에 보낼 길이(프레임 + 앞에 메울 무음, ms)를 받아 보내도 되는지 답한다
        (`AudioBudget.take`). 안 된다면 버리고 그 구간(틈 + 프레임)을 `lost_ms` 로 센다. 다음 틈은
        버린 프레임 뒤부터 잰다 — 버린 구간을 무음으로 메우면 버린 만큼 다시 보내고, 세션을 갈면
        버릴 때마다 Deepgram 연결이 하나씩 더 생긴다. 대신 그 세션의 뒤 전사 시각이 버린 만큼
        앞당겨진다.
        정상 FE 는 실시간으로 보내 여기 오지 않는다.
        새 연결의 첫 프레임(새로고침일 수 있다)은 순서 상태를 그대로 두고 다음 프레임이 다시 가린다.
        """
        last = self._last
        restart = False
        if self._new_connection:
            # 재연결은 seq·offset 을 이어 보낸다 (FE 는 보낸 프레임을 다시 보내지 않는다 — README 의
            # STT 규약. 이 약속이 깨지면 다시 보낸 프레임을 새로고침으로 오인한다). 새 연결의
            # 첫 프레임이 뒤로 갔다면 FE 가 처음부터 다시 센 것이다 — 탭을 새로고침하면 seq 는
            # 1 부터, offset 은 이어받은 무대 시계부터 다시 센다. 역행으로 버리면 그 뒤 오디오가
            # 예전 seq 를 넘을 때까지 통째로 사라진다.
            # offset 도 보는 이유: 이전 탭이 보낸 프레임이 적으면 새 탭의 첫 seq 가 더 클 수 있고,
            # 이어받은 시점이 추정값이면(탭이 죽은 경우) 이전 탭이 보낸 시각보다 앞설 수 있다
            restart = last is not None and (
                frame.seq <= last.seq or frame.offset_ms < last.offset_ms
            )
            if restart:
                last = None

        if last is not None and frame.seq <= last.seq:
            self._new_connection = False
            self.dropped += 1
            return None

        silence_ms = 0
        lost_ms = 0
        if last is not None:
            gap_ms = frame.offset_ms - last.end_ms
            if gap_ms > MAX_SILENCE_FILL_MS:
                # 부분만 채우면 타임라인이 어긋난다. 아예 채우지 않고 세션 교체를 요구한다
                lost_ms = gap_ms
            elif gap_ms > GAP_TOLERANCE_MS:
                silence_ms = gap_ms
            # 음수(겹침) 는 지터다. 그대로 이어 보낸다

        if allow is not None and not allow(silence_ms + frame.duration_ms):
            self.dropped += 1
            self.over_budget += 1
            if not self._new_connection:
                self._last = frame
                self.lost_ms += silence_ms + lost_ms + frame.duration_ms
            return None

        self._new_connection = False
        self._last = frame
        self.frames += 1
        self.silence_ms += silence_ms
        self.lost_ms += lost_ms
        return Accepted(frame=frame, silence_ms=silence_ms, lost_ms=lost_ms, restart=restart)
