import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  clearWriteFailures,
  hasWriteFailures,
  noteWriteFailure,
  readWriteFailures,
} from './writeFailures';

const SESSION = 'session-1';

afterEach(() => {
  clearWriteFailures(SESSION);
  clearWriteFailures('other');
  vi.restoreAllMocks();
});

describe('writeFailures', () => {
  it('실패가 없으면 빈 장부다', () => {
    expect(readWriteFailures(SESSION)).toEqual({});
    expect(hasWriteFailures(SESSION)).toBe(false);
  });

  it('첫 건에만 true 를 돌려준다 — 호출부의 "한 번만" 이 여기 걸립니다', () => {
    expect(noteWriteFailure(SESSION, 'gazeDecision', new Error('quota'))).toBe(true);
    expect(noteWriteFailure(SESSION, 'gazeDecision', new Error('quota'))).toBe(false);
    expect(noteWriteFailure(SESSION, 'gazeDecision', new Error('quota'))).toBe(false);
  });

  it('종류가 다르면 각각 첫 건이다', () => {
    expect(noteWriteFailure(SESSION, 'slideChange', null)).toBe(true);
    expect(noteWriteFailure(SESSION, 'gazeDecision', null)).toBe(true);
  });

  it('종류별로 센다', () => {
    noteWriteFailure(SESSION, 'gazeDecision', null);
    noteWriteFailure(SESSION, 'gazeDecision', null);
    noteWriteFailure(SESSION, 'slideChange', null);

    expect(readWriteFailures(SESSION)).toEqual({ gazeDecision: 2, slideChange: 1 });
    expect(hasWriteFailures(SESSION)).toBe(true);
  });

  it('세션이 섞이지 않는다 — 한 탭에서 Take 를 여러 번 합니다', () => {
    noteWriteFailure(SESSION, 'gazeDecision', null);
    noteWriteFailure('other', 'coachLog', null);

    expect(readWriteFailures(SESSION)).toEqual({ gazeDecision: 1 });
    expect(readWriteFailures('other')).toEqual({ coachLog: 1 });
  });

  it('비우면 다음 실패가 다시 첫 건이 된다', () => {
    noteWriteFailure(SESSION, 'gazeDecision', null);
    clearWriteFailures(SESSION);

    expect(readWriteFailures(SESSION)).toEqual({});
    expect(noteWriteFailure(SESSION, 'gazeDecision', null)).toBe(true);
  });

  it('콘솔에는 첫 건만 찍는다 — 저장소가 차면 1초마다 같은 실패가 옵니다', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    noteWriteFailure(SESSION, 'gazeDecision', new Error('quota'));
    noteWriteFailure(SESSION, 'gazeDecision', new Error('quota'));
    noteWriteFailure(SESSION, 'gazeDecision', new Error('quota'));

    expect(spy).toHaveBeenCalledTimes(1);
  });
});
