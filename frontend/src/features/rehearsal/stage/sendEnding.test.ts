import { afterEach, describe, expect, it, vi } from 'vitest';
import { endSession, markSubmitted } from '../lib/db';
import { sendEnding } from './sendEnding';

/** 테스트는 Node 에서 돌아 IndexedDB 가 없습니다. 세션 행만 흉내 냅니다 */
vi.mock('../lib/db', () => ({
  endSession: vi.fn(async () => undefined),
  markSubmitted: vi.fn(async () => undefined),
}));

const ENDING = { durationMs: 60_000, endedAtIso: '2026-10-25T01:01:00.000Z' };

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe('종료 보내기', () => {
  it('세션을 끝낸 뒤 시작 · 종료 시각을 보내고 보냈다고 적는다', async () => {
    const endTake = vi.fn(async () => undefined);
    await sendEnding('t1', 's1', ENDING, endTake);

    expect(endSession).toHaveBeenCalledWith('s1');
    expect(endTake).toHaveBeenCalledWith({
      started_at: '2026-10-25T01:00:00.000Z',
      ended_at: ENDING.endedAtIso,
    });
    expect(markSubmitted).toHaveBeenCalledWith('s1');
  });

  it('보내기가 실패하면 던지고 보냈다고 적지 않는다 — 재시도 화면이 다시 부릅니다', async () => {
    const endTake = vi.fn(async () => {
      throw new Error('network');
    });
    await expect(sendEnding('t1', 's1', ENDING, endTake)).rejects.toThrow('network');
    expect(markSubmitted).not.toHaveBeenCalled();
  });

  it('종료 재시도가 늦어져도 고정한 종료 시각과 발표 길이로 시작을 계산한다', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-25T02:00:00.000Z'));
    const endTake = vi.fn(async () => undefined);
    await sendEnding('t1', 's1', ENDING, endTake);

    expect(endTake).toHaveBeenCalledWith({
      started_at: '2026-10-25T01:00:00.000Z',
      ended_at: ENDING.endedAtIso,
    });
  });
});
