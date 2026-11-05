import { beforeEach, describe, expect, it } from 'vitest';
import {
  DEFAULT_SCRIPT_MODE,
  normalizeScriptMode,
  practiceModeFor,
  usePrepareStore,
} from './prepareStore';

describe('서버가 준 대본 표시 값 접기', () => {
  it('3단계는 그대로 둔다', () => {
    expect(normalizeScriptMode('HIGHLIGHT')).toBe('HIGHLIGHT');
    expect(normalizeScriptMode('KEYWORD')).toBe('KEYWORD');
    expect(normalizeScriptMode('OFF')).toBe('OFF');
  });

  /** BE 가 아직 3단계로 안 맞춰져 있습니다 */
  it('없어진 FULL 은 HIGHLIGHT 로 간다', () => {
    expect(normalizeScriptMode('FULL')).toBe('HIGHLIGHT');
  });

  /**
   * ★ 모르는 값이 OFF 로 떨어지면 안 됩니다 — 발표자가 대본 없이 시작하게 됩니다.
   *   게다가 지금은 OFF 가 실전 모드라, 코치까지 조용해집니다.
   */
  it('모르는 값과 빈 값은 HIGHLIGHT 로 떨어진다 — OFF 가 아니다', () => {
    for (const raw of ['', 'WHATEVER', null, undefined]) {
      expect(normalizeScriptMode(raw)).toBe('HIGHLIGHT');
    }
  });
});

describe('대본 표시에서 연습 모드 정하기 (시안 09)', () => {
  it('대본 없이를 고르면 실전 모드다', () => {
    expect(practiceModeFor('OFF')).toBe('EXAM');
  });

  it('대본을 띄우면 코칭 모드다', () => {
    expect(practiceModeFor('HIGHLIGHT')).toBe('COACHING');
    expect(practiceModeFor('KEYWORD')).toBe('COACHING');
  });
});

describe('피치가 바뀌면 대본 표시를 비운다', () => {
  const store = () => usePrepareStore.getState();
  beforeEach(() => store().reset());

  it('피치 A 에서 고른 실전 모드가 피치 B 에 따라오지 않는다 — 서버 기본값이 들어갈 수 있게', () => {
    store().enterPitch('a');
    store().setScriptMode('OFF');
    store().enterPitch('b');

    expect(store().scriptMode).toBe(DEFAULT_SCRIPT_MODE);
    expect(store().scriptModeTouched).toBe(false);
    expect(store().scriptModePitchId).toBe('b');
  });

  it('같은 피치로 다시 들어오면 고른 값을 그대로 둔다', () => {
    store().enterPitch('a');
    store().setScriptMode('KEYWORD');
    store().enterPitch('a');

    expect(store().scriptMode).toBe('KEYWORD');
    expect(store().scriptModeTouched).toBe(true);
  });

  it('장치 선택은 피치가 바뀌어도 남는다', () => {
    store().enterPitch('a');
    store().setDevices({ videoDeviceId: 'cam', audioDeviceId: 'mic' });
    store().enterPitch('b');

    expect(store().devices).toEqual({ videoDeviceId: 'cam', audioDeviceId: 'mic' });
  });
});
