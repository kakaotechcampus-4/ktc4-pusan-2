/**
 * STT 로 흘려보내는 오디오의 규격과 변환.
 *
 * ── 오디오 경로가 둘인 이유 ─────────────────────────────────────────
 * `MediaRecorder` 가 뱉는 webm/opus 는 **스트리밍 STT 에 못 씁니다** — 컨테이너에
 * 싸인 압축 포맷이라 조각만 떼어 디코딩할 수 없습니다. 그래서 원본 보관(recorder.ts)
 * 과 별개로, 여기서 raw PCM 을 따로 뽑습니다.
 *
 * ── 값이 틀리면 에러가 아니라 쓰레기 전사가 나옵니다 ────────────────
 * BE `realtime/audio.py` 가 확정한 값입니다. 샘플레이트가 틀리면 재생 속도가 바뀐
 * 것처럼 들리고, 엔디언이나 채널 수가 틀리면 잡음이 됩니다. **어느 쪽도 예외를
 * 던지지 않습니다.** 그래서 이 파일은 테스트가 있습니다.
 */

/** BE audio.py 와 같은 값. 한쪽만 바꾸면 전사가 조용히 망가집니다 */
export const SAMPLE_RATE = 16_000;
export const CHANNELS = 1;
export const SAMPLE_WIDTH_BYTES = 2;
/** 16kHz × 2바이트 × 모노 = 32 */
export const BYTES_PER_MS = (SAMPLE_RATE * SAMPLE_WIDTH_BYTES * CHANNELS) / 1000;

/**
 * 한 프레임에 담는 길이.
 *
 * AudioWorklet 은 128 샘플(16kHz 에서 8ms)씩 부릅니다. 그대로 보내면 초당 125 번이라
 * 오버헤드가 큽니다. BE 도 100ms 를 전제로 큐 크기를 잡아 두었습니다
 * (`QUEUE_MAX_FRAMES = 50` → 5초).
 */
export const FRAME_MS = 100;
export const SAMPLES_PER_FRAME = (SAMPLE_RATE * FRAME_MS) / 1000;

/** [seq: uint32 LE][offset_ms: uint32 LE] */
export const HEADER_BYTES = 8;

/** BE 가 이보다 긴 프레임을 오용으로 봅니다 (event_ingestion.MAX_FRAME_MS) */
export const MAX_FRAME_MS = 1_000;

const UINT32_MAX = 0xff_ff_ff_ff;

/**
 * Float32(-1..1) → Int16(-32768..32767).
 *
 * ★ 범위를 먼저 자릅니다. 넘는 값을 그냥 곱하면 숫자가 한 바퀴 돌아 **가장 큰 양수가
 *   가장 작은 음수**가 됩니다 — 소리로는 "탁" 하는 파열음이 되고, 전사에서는 그 구간이
 *   통째로 깨집니다.
 *
 * 음수는 32768, 양수는 32767 을 곱합니다. Int16 이 음수 쪽으로 한 칸 더 넓기 때문입니다.
 */
export function floatToInt16(input: Float32Array, out?: Int16Array): Int16Array {
  const result = out ?? new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]!));
    result[i] = s < 0 ? s * 0x80_00 : s * 0x7f_ff;
  }
  return result;
}

/**
 * BE 로 보낼 바이너리 프레임 하나.
 *
 *     [seq: uint32 LE][offset_ms: uint32 LE][PCM: Int16 LE ...]
 *
 * ★ `DataView` 로 바이트를 직접 씁니다. `Int16Array` 의 메모리를 그대로 보내면
 *   **플랫폼 엔디언에 의존**합니다. 요즘 기기는 대부분 리틀엔디언이라 당장은 통하지만,
 *   빅엔디언에서는 에러 없이 잡음이 됩니다 — 그런 종류의 버그는 원인을 못 찾습니다.
 *
 * @param seq       1 부터 올라가는 순서표. **줄어들면 BE 가 조용히 버립니다**
 * @param offsetMs  Take 시작 기준 경과 ms (`performance.now()`).
 *                  시선 `tMs`·녹음 `offsetMs` 와 **같은 시계**여야 나중에 겹쳐 볼 수 있습니다
 */
export function encodeAudioFrame(seq: number, offsetMs: number, pcm: Int16Array): ArrayBuffer {
  if (!Number.isInteger(seq) || seq < 0 || seq > UINT32_MAX) {
    throw new RangeError(`seq 가 uint32 범위를 벗어났습니다: ${seq}`);
  }
  if (!Number.isInteger(offsetMs) || offsetMs < 0 || offsetMs > UINT32_MAX) {
    throw new RangeError(`offsetMs 가 uint32 범위를 벗어났습니다: ${offsetMs}`);
  }
  if (pcm.length === 0) {
    throw new RangeError('빈 프레임은 보내지 않습니다.');
  }
  if (pcm.length > (MAX_FRAME_MS * BYTES_PER_MS) / SAMPLE_WIDTH_BYTES) {
    throw new RangeError(`프레임이 ${MAX_FRAME_MS}ms 를 넘었습니다: ${pcm.length} 샘플`);
  }

  const buffer = new ArrayBuffer(HEADER_BYTES + pcm.length * SAMPLE_WIDTH_BYTES);
  const view = new DataView(buffer);
  view.setUint32(0, seq, true);
  view.setUint32(4, offsetMs, true);
  for (let i = 0; i < pcm.length; i++) {
    view.setInt16(HEADER_BYTES + i * SAMPLE_WIDTH_BYTES, pcm[i]!, true);
  }
  return buffer;
}

/**
 * 인코딩의 역. **보내는 경로에는 쓰지 않습니다** — 테스트와 디버깅용입니다.
 * 왕복이 맞는지 확인할 수 있어야 형식이 틀렸을 때 빨리 드러납니다.
 */
export function decodeAudioFrame(buffer: ArrayBuffer): {
  seq: number;
  offsetMs: number;
  pcm: Int16Array;
} {
  if (buffer.byteLength < HEADER_BYTES + SAMPLE_WIDTH_BYTES) {
    throw new RangeError('프레임이 너무 짧습니다.');
  }
  const view = new DataView(buffer);
  const samples = (buffer.byteLength - HEADER_BYTES) / SAMPLE_WIDTH_BYTES;
  const pcm = new Int16Array(samples);
  for (let i = 0; i < samples; i++) {
    pcm[i] = view.getInt16(HEADER_BYTES + i * SAMPLE_WIDTH_BYTES, true);
  }
  return { seq: view.getUint32(0, true), offsetMs: view.getUint32(4, true), pcm };
}

/** 샘플 수 → ms. 갭 계산과 다음 offset 을 잡을 때 씁니다 */
export function samplesToMs(samples: number): number {
  return Math.round((samples / SAMPLE_RATE) * 1000);
}
