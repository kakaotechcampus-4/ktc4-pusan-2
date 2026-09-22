import { describe, expect, it } from 'vitest';
// ?raw 로 읽습니다 — node:fs 를 쓰면 앱 tsconfig 에 node 타입을 끌어들여야 합니다
import workletSource from '../../../../public/worklets/pcm.js?raw';
import {
  BYTES_PER_MS,
  FRAME_MS,
  HEADER_BYTES,
  MAX_FRAME_MS,
  SAMPLES_PER_FRAME,
  SAMPLE_RATE,
  decodeAudioFrame,
  encodeAudioFrame,
  floatToInt16,
  samplesToMs,
} from './pcm';

describe('BE 와 합의한 상수', () => {
  // audio.py 가 한쪽만 바뀌면 에러 없이 쓰레기 전사가 나옵니다. 숫자를 못 박아 둡니다.
  it('BYTES_PER_MS 는 32 다 — 16kHz × 2바이트 × 모노', () => {
    expect(BYTES_PER_MS).toBe(32);
  });

  it('100ms 프레임은 1600 샘플 · 3200 바이트다', () => {
    expect(SAMPLES_PER_FRAME).toBe(1600);
    expect(SAMPLES_PER_FRAME * 2).toBe(FRAME_MS * BYTES_PER_MS);
  });

  it('samplesToMs 가 상수와 일관된다', () => {
    expect(samplesToMs(SAMPLES_PER_FRAME)).toBe(FRAME_MS);
    expect(samplesToMs(SAMPLE_RATE)).toBe(1000);
  });
});

describe('Float32 → Int16', () => {
  it('0 은 0 이다', () => {
    expect(Array.from(floatToInt16(new Float32Array([0])))).toEqual([0]);
  });

  it('양끝이 Int16 양끝으로 간다', () => {
    const out = floatToInt16(new Float32Array([1, -1]));
    expect(out[0]).toBe(32767);
    expect(out[1]).toBe(-32768);
  });

  /**
   * ★ 자르지 않으면 숫자가 한 바퀴 돌아 가장 큰 양수가 가장 작은 음수가 됩니다.
   *   소리로는 "탁" 하는 파열음이고, 그 구간 전사가 통째로 깨집니다.
   */
  it('범위를 넘는 값은 잘린다 — 한 바퀴 돌지 않는다', () => {
    const out = floatToInt16(new Float32Array([2.5, -2.5, 1.0001, -1.0001]));
    expect(Array.from(out)).toEqual([32767, -32768, 32767, -32768]);
  });

  it('중간값이 비례한다', () => {
    const out = floatToInt16(new Float32Array([0.5, -0.5]));
    expect(out[0]).toBe(Math.trunc(0.5 * 32767));
    expect(out[1]).toBe(Math.trunc(-0.5 * 32768));
  });

  it('길이를 보존한다', () => {
    expect(floatToInt16(new Float32Array(1600))).toHaveLength(1600);
  });

  it('out 을 주면 거기에 쓴다 — 매 프레임 할당하지 않기 위한 것', () => {
    const out = new Int16Array(2);
    const result = floatToInt16(new Float32Array([1, -1]), out);
    expect(result).toBe(out);
    expect(Array.from(out)).toEqual([32767, -32768]);
  });
});

describe('프레임 인코딩', () => {
  const pcm = new Int16Array([0, 1, -1, 32767, -32768]);

  it('헤더 8바이트 + PCM 2바이트씩이다', () => {
    const buf = encodeAudioFrame(1, 0, pcm);
    expect(buf.byteLength).toBe(HEADER_BYTES + pcm.length * 2);
  });

  it('seq 와 offset_ms 가 리틀엔디언 uint32 로 들어간다', () => {
    const buf = encodeAudioFrame(0x01020304, 0x0a0b0c0d, pcm);
    const bytes = new Uint8Array(buf);

    // 리틀엔디언이므로 낮은 바이트가 먼저 옵니다
    expect(Array.from(bytes.slice(0, 4))).toEqual([0x04, 0x03, 0x02, 0x01]);
    expect(Array.from(bytes.slice(4, 8))).toEqual([0x0d, 0x0c, 0x0b, 0x0a]);
  });

  it('PCM 도 리틀엔디언이다 — 플랫폼 엔디언에 기대지 않는다', () => {
    const buf = encodeAudioFrame(1, 0, new Int16Array([0x0102]));
    const bytes = new Uint8Array(buf);
    expect(Array.from(bytes.slice(HEADER_BYTES))).toEqual([0x02, 0x01]);
  });

  it('왕복하면 그대로 돌아온다', () => {
    const decoded = decodeAudioFrame(encodeAudioFrame(42, 12_345, pcm));
    expect(decoded.seq).toBe(42);
    expect(decoded.offsetMs).toBe(12_345);
    expect(Array.from(decoded.pcm)).toEqual(Array.from(pcm));
  });

  it('100ms 프레임이 BE 가 세는 길이와 맞는다', () => {
    const buf = encodeAudioFrame(1, 0, new Int16Array(SAMPLES_PER_FRAME));
    // BE 는 (전체 - 헤더) / BYTES_PER_MS 로 길이를 잽니다
    expect((buf.byteLength - HEADER_BYTES) / BYTES_PER_MS).toBe(FRAME_MS);
  });
});

describe('BE 가 거절하는 프레임은 보내기 전에 막는다', () => {
  it('빈 프레임', () => {
    expect(() => encodeAudioFrame(1, 0, new Int16Array(0))).toThrow(RangeError);
  });

  it('1000ms 를 넘는 프레임 — BE 는 오용으로 본다', () => {
    const tooLong = new Int16Array((MAX_FRAME_MS * BYTES_PER_MS) / 2 + 1);
    expect(() => encodeAudioFrame(1, 0, tooLong)).toThrow(RangeError);
  });

  it('딱 1000ms 는 통과한다', () => {
    const exact = new Int16Array((MAX_FRAME_MS * BYTES_PER_MS) / 2);
    expect(() => encodeAudioFrame(1, 0, exact)).not.toThrow();
  });

  it('uint32 를 넘는 seq · offsetMs', () => {
    const pcm = new Int16Array([1]);
    expect(() => encodeAudioFrame(2 ** 32, 0, pcm)).toThrow(RangeError);
    expect(() => encodeAudioFrame(-1, 0, pcm)).toThrow(RangeError);
    expect(() => encodeAudioFrame(1, 2 ** 32, pcm)).toThrow(RangeError);
    expect(() => encodeAudioFrame(1, 1.5, pcm)).toThrow(RangeError);
  });
});

/**
 * 워크릿은 `public/worklets/` 의 평범한 JS 라 타입 검사를 못 받습니다
 * (CLAUDE.md 7번 — 번들러를 태우면 경로가 해시로 바뀌어 런타임에 못 찾습니다).
 *
 * 값이 어긋나면 **에러가 아니라** 프레임 길이가 달라져 BE 의 갭 계산이 틀어집니다.
 * 그래서 소스를 읽어 숫자를 맞춰 봅니다.
 */
describe('워크릿과 상수가 어긋나지 않는다', () => {
  const source = workletSource;

  it('SAMPLES_PER_FRAME 이 pcm.ts 와 같다', () => {
    const m = /const SAMPLES_PER_FRAME = (\d+);/.exec(source);
    expect(m, '워크릿에서 SAMPLES_PER_FRAME 선언을 못 찾았습니다').not.toBeNull();
    expect(Number(m![1])).toBe(SAMPLES_PER_FRAME);
  });

  it('pcmCapture 가 쓰는 이름으로 등록한다', () => {
    expect(source).toContain("registerProcessor('pcm-frame'");
  });

  it('import 문이 없다 — AudioWorklet 에서 도는지 브라우저마다 갈린다', () => {
    expect(/^\s*import\s/m.test(source)).toBe(false);
  });
});
