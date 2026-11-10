import { afterEach, describe, expect, it } from 'vitest';
import {
  BASE_VOICE_REUSE_MS,
  forgetBaseVoice,
  keepBaseVoice,
  recallBaseVoice,
} from './baseVoiceMemory';

const mic = { deviceId: 'mic-a', groupId: 'group-a', autoGainOff: true };

afterEach(() => forgetBaseVoice());

describe('기준 목소리 다시 쓰기 (#1179)', () => {
  it('같은 마이크 · 자동 음량 조절 꺼짐이면 30분 안에는 다시 쓴다', () => {
    keepBaseVoice(-31.2, mic, 1000);
    expect(recallBaseVoice(mic, 1000 + BASE_VOICE_REUSE_MS - 1)).toEqual({
      levelDb: -31.2,
      measuredAt: 1000,
    });
  });

  it('처음 잰 시각부터 30분이 지나면 다시 쓰지 않는다 — 다시 써도 시각은 그대로다', () => {
    keepBaseVoice(-31.2, mic, 1000);
    // 중간에 다시 썼어도 시각을 갱신하지 않아, 처음 잰 때부터 셉니다
    expect(recallBaseVoice(mic, 1000 + BASE_VOICE_REUSE_MS / 2)?.measuredAt).toBe(1000);
    expect(recallBaseVoice(mic, 1000 + BASE_VOICE_REUSE_MS)).toBeNull();
  });

  it('다른 마이크면 다시 쓰지 않는다', () => {
    keepBaseVoice(-31.2, mic, 1000);
    expect(
      recallBaseVoice({ deviceId: 'mic-b', groupId: 'group-a', autoGainOff: true }, 2000),
    ).toBeNull();
  });

  it('지금 마이크의 자동 음량 조절이 켜졌거나 모르면 다시 쓰지 않는다', () => {
    keepBaseVoice(-31.2, mic, 1000);
    expect(
      recallBaseVoice({ deviceId: 'mic-a', groupId: 'group-a', autoGainOff: false }, 2000),
    ).toBeNull();
    expect(
      recallBaseVoice({ deviceId: 'mic-a', groupId: 'group-a', autoGainOff: null }, 2000),
    ).toBeNull();
    expect(recallBaseVoice(null, 2000)).toBeNull();
  });

  it('장치를 모르거나(빈 문자열) 자동 음량 조절이 켜진 채 잰 값은 저장하지 않는다', () => {
    keepBaseVoice(-31.2, { deviceId: '', groupId: 'group-a', autoGainOff: true }, 1000);
    expect(
      recallBaseVoice({ deviceId: '', groupId: 'group-a', autoGainOff: true }, 2000),
    ).toBeNull();
    keepBaseVoice(-31.2, { deviceId: 'mic-a', groupId: 'group-a', autoGainOff: false }, 1000);
    expect(recallBaseVoice(mic, 2000)).toBeNull();
    keepBaseVoice(-31.2, { deviceId: 'mic-a', groupId: 'group-a', autoGainOff: null }, 1000);
    expect(recallBaseVoice(mic, 2000)).toBeNull();
  });

  it('새로 재면 그 값과 시각으로 바꾼다', () => {
    keepBaseVoice(-31.2, mic, 1000);
    keepBaseVoice(-27.5, mic, 5000);
    expect(recallBaseVoice(mic, 6000)).toEqual({ levelDb: -27.5, measuredAt: 5000 });
  });
});

// #1184 리뷰 — 크롬의 장치 별칭은 실제 기기를 가리키지 않아, OS 기본 마이크가 바뀌어도 id 가 같습니다
describe('장치 별칭과 실제 기기 (#1184)', () => {
  it.each(['default', 'communications'])(
    '별칭(%s)으로 잰 값은 저장하지도 다시 쓰지도 않는다',
    (alias) => {
      const aliased = { deviceId: alias, groupId: 'group-a', autoGainOff: true };
      keepBaseVoice(-31.2, aliased, 1000);
      expect(recallBaseVoice(aliased, 2000)).toBeNull();
      // 앞서 실제 id 로 잰 값이 있어도 별칭인 지금 마이크에는 권하지 않습니다
      keepBaseVoice(-31.2, mic, 1000);
      expect(recallBaseVoice(aliased, 2000)).toBeNull();
    },
  );

  it('id 가 같아도 실제 기기 묶음(groupId)이 다르면 다시 쓰지 않는다', () => {
    keepBaseVoice(-31.2, mic, 1000);
    expect(recallBaseVoice({ ...mic, groupId: 'group-b' }, 2000)).toBeNull();
  });
});
