import { describe, expect, it } from 'vitest';
import {
  buildAudioFrame,
  FRAME_BYTES,
  FRAME_SAMPLES,
  isFatalError,
  parseServerMessage,
} from './sttProtocol';

describe('오디오 프레임', () => {
  it('헤더를 little-endian 으로 쓰고 PCM 을 뒤에 붙인다', () => {
    const pcm = new Int16Array(FRAME_SAMPLES);
    pcm[0] = -32_768;
    pcm[FRAME_SAMPLES - 1] = 32_767;

    const frame = buildAudioFrame(7, 1_200, pcm.buffer);
    const header = new DataView(frame.buffer, frame.byteOffset, 8);

    expect(frame.byteLength).toBe(FRAME_BYTES);
    expect(header.getUint32(0, true)).toBe(7);
    expect(header.getUint32(4, true)).toBe(1_200);

    // 서버는 struct("<II") 로 읽습니다 — big-endian 으로 쓰면 seq 가 1.7천만이 됩니다
    expect(header.getUint32(0, false)).not.toBe(7);

    const payload = new Int16Array(frame.buffer.slice(8));
    expect(payload[0]).toBe(-32_768);
    expect(payload[FRAME_SAMPLES - 1]).toBe(32_767);
  });
});

describe('서버 메시지 파싱', () => {
  it('아는 메시지만 통과시킨다', () => {
    const transcript = parseServerMessage(
      JSON.stringify({ type: 'transcript', segment_id: '1-7', is_final: true, text: '안녕하세요' }),
    );

    expect(transcript).toMatchObject({ type: 'transcript', segment_id: '1-7' });
  });

  it('모르는 메시지와 깨진 입력은 null 이다 — 발표 중에 예외가 나면 그걸로 끝이다', () => {
    expect(parseServerMessage(JSON.stringify({ type: 'metrics', wpm: 120 }))).toBeNull();
    expect(parseServerMessage('{ 이건 JSON 이 아니다')).toBeNull();
    expect(parseServerMessage(new ArrayBuffer(8))).toBeNull();
    expect(parseServerMessage(JSON.stringify(null))).toBeNull();
  });
});

describe('에러 코드', () => {
  it('연결을 닫는 코드와 유지하는 코드를 가른다', () => {
    expect(isFatalError('TAKE_ENDED')).toBe(true);
    expect(isFatalError('TAKE_TAKEN_OVER')).toBe(true);
    expect(isFatalError('BAD_AUDIO_FRAME')).toBe(false);
    expect(isFatalError('BAD_MESSAGE')).toBe(false);
  });
});
