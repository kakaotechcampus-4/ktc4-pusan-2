"""서버로 들어오는 오디오의 규격.

FE AudioWorklet → BE → Deepgram 이 전부 이 값을 전제한다 (테크스펙 확정값). 프레임 파서와
Deepgram 어댑터가 **같은 값**을 봐야 하므로 한쪽에 두지 않고 여기에 모은다.

하나라도 어긋나면 에러 없이 **쓰레기 전사**가 나온다 — 샘플레이트가 틀리면 재생 속도가
바뀐 것처럼 들리고, 엔디언이나 채널 수가 틀리면 잡음이 된다. 그래서 값을 바꾸려면
FE worklet 과 Deepgram 쿼리 파라미터를 함께 고쳐야 한다.
"""

SAMPLE_RATE = 16_000
CHANNELS = 1
# 16-bit signed little-endian. Deepgram 의 linear16 이 요구하는 형식이다
SAMPLE_WIDTH_BYTES = 2
ENCODING = "linear16"

# 16 kHz × 2 bytes × mono = 32 bytes/ms. offset 계산과 무음 채우기의 기준 상수
BYTES_PER_MS = SAMPLE_RATE * SAMPLE_WIDTH_BYTES * CHANNELS // 1000


def silence(duration_ms: int) -> bytes:
    """0 으로 채운 PCM. Deepgram 에 '아무 말도 없었다' 를 시간 그대로 전달한다."""
    return bytes(duration_ms * BYTES_PER_MS)
