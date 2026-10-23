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

  it('서버의 자동 종료 알림을 통과시킨다', () => {
    const ended = parseServerMessage(
      JSON.stringify({ type: 'auto_end', reason: 'SILENCE', message: '2분 넘게 말이 없어요' }),
    );

    expect(ended).toEqual({ type: 'auto_end', reason: 'SILENCE', message: '2분 넘게 말이 없어요' });
  });

  it('에러의 끝낸 이유를 통과시키고, 없거나 모르는 이유는 null 로 둔다', () => {
    const error = (extra: object) =>
      parseServerMessage(
        JSON.stringify({ type: 'error', code: 'TAKE_ENDED', message: '끝남', ...extra }),
      );

    expect(error({ end_reason: 'CLOSED' })).toMatchObject({ end_reason: 'CLOSED' });
    expect(error({ end_reason: 'ABANDONED' })).toMatchObject({ end_reason: 'ABANDONED' });
    // 사용자가 끝낸 Take 는 서버가 필드를 뺍니다
    expect(error({})).toMatchObject({ end_reason: null });
    // 서버가 나중에 늘린 값은 분기할 수 없습니다
    expect(error({ end_reason: 'SOMETHING_NEW' })).toMatchObject({ end_reason: null });
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
