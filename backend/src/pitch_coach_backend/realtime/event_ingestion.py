"""FE -> BE 오디오 프레임의 검증·정규화·중복 제거. 네트워크도 Deepgram 도 모른다.

프레임 형식 (binary frame 하나):

    [seq: uint32 LE][offset_ms: uint32 LE][PCM: 16-bit signed LE, 16 kHz, mono]

offset_ms 는 Take 시작(RUNNING 이 된 시각) 기준이고 FE 가 performance.now() 로 잰다.
BE 는 이 값으로 (1) 순서 꼬임·중복을 버리고 (2) 끊긴 구간을 알아내 무음으로 채운다.

왜 채우는가: Deepgram 타임스탬프는 "그 연결에 보낸 첫 바이트 = 0" 이다. 중간에 빠진
오디오가 있으면 그 뒤의 모든 타임스탬프가 빠진 만큼 앞당겨진다. 갭을 무음으로 채우면
한 연결 안에서는 Deepgram 타임라인과 Take 타임라인이 같다. 무음도 과금되지만 몇 초 수준이다.
"""

import struct
from dataclasses import dataclass

from pitch_coach_backend.realtime.audio import BYTES_PER_MS

FRAME_HEADER = struct.Struct("<II")

# 1초 넘는 프레임은 재연결 flush 가 아니라 오용으로 본다 (FE 버퍼는 1~2초지만 프레임 단위로 보낸다)
MAX_FRAME_MS = 1_000
MAX_FRAME_BYTES = FRAME_HEADER.size + MAX_FRAME_MS * BYTES_PER_MS

# performance.now() 지터. 이 이하로 어긋난 건 갭이 아니라 타이머 흔들림이다
GAP_TOLERANCE_MS = 50

# 이보다 큰 갭은 채우지 않는다. 몇 분짜리 무음을 밀어 넣으면 그동안 실시간 전사가 멈춘다.
# 못 채운 만큼은 lost_ms 로 기록한다 — 그 뒤 타임스탬프는 그만큼 앞당겨져 있다.
MAX_SILENCE_FILL_MS = 5_000


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
    # 무음으로 채우지 못하고 잃은 길이. 0 이 아니면 이후 타임스탬프가 이만큼 앞당겨져 있다
    lost_ms: int


class FrameSequencer:
    """프레임 순서를 추적하고 중복·역행을 버린다. 통계는 stt_status 로 FE 에 보낸다."""

    def __init__(self) -> None:
        self.frames = 0
        self.dropped = 0
        self.silence_ms = 0
        self.lost_ms = 0
        self._last: AudioFrame | None = None

    def accept(self, frame: AudioFrame) -> Accepted | None:
        """받아들이면 Accepted, 버리면 None."""
        if self._last is not None and frame.seq <= self._last.seq:
            self.dropped += 1
            return None

        silence_ms = 0
        lost_ms = 0
        if self._last is not None:
            gap_ms = frame.offset_ms - self._last.end_ms
            if gap_ms > GAP_TOLERANCE_MS:
                silence_ms = min(gap_ms, MAX_SILENCE_FILL_MS)
                lost_ms = gap_ms - silence_ms
            # 음수(겹침) 는 지터다. 그대로 이어 보낸다

        self._last = frame
        self.frames += 1
        self.silence_ms += silence_ms
        self.lost_ms += lost_ms
        return Accepted(frame=frame, silence_ms=silence_ms, lost_ms=lost_ms)
