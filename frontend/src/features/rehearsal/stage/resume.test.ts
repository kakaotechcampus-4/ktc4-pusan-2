import { afterEach, describe, expect, it, vi } from 'vitest';
import { PresentationClock } from '@/shared/lib/clock';
import { coachHistory, lastSlide, recordedUntilMs, resumeFromMs } from './resume';

const BEAT_MS = 5_000;
const row = (elapsedMs: number, lastBeatAt: number) => ({
  clientSessionId: 's-1',
  elapsedMs,
  lastBeatAt,
});

describe('무대 시계를 어디서부터 다시 돌릴까', () => {
  it('새로고침 직전에 적어 둔 값이 있으면 그 값이다', () => {
    const saved = { clientSessionId: 's-1', elapsedMs: 63_400 };
    expect(resumeFromMs(row(60_000, 1_000), saved, 1_000 + 9_000, BEAT_MS)).toBe(63_400);
  });

  /** 같은 Take 를 두 번 시작한 세션이 있으면 다른 세션의 값을 이어받으면 안 됩니다 */
  it('다른 세션이 적어 둔 값은 쓰지 않는다', () => {
    const saved = { clientSessionId: 's-other', elapsedMs: 999_000 };
    expect(resumeFromMs(row(60_000, 1_000), saved, 1_000 + 2_000, BEAT_MS)).toBe(62_000);
  });

  it('적어 둔 값이 없으면 마지막 하트비트 뒤로 흐른 만큼 더한다', () => {
    expect(resumeFromMs(row(60_000, 1_000), null, 1_000 + 3_200, BEAT_MS)).toBe(63_200);
  });

  /** 다음 날 같은 주소로 들어와도 시간이 하루만큼 튀지 않습니다 */
  it('하트비트 뒤로 흐른 시간은 한 주기까지만 더한다', () => {
    expect(resumeFromMs(row(60_000, 1_000), null, 1_000 + 86_400_000, BEAT_MS)).toBe(65_000);
  });

  /**
   * 준비 화면에서 막 넘어온 행은 lastBeatAt 이 Take 를 만든 시각입니다.
   * 그대로 더하면 처음 시작하는 발표가 몇 초부터 시작합니다.
   */
  it('하트비트가 한 번도 안 찍혔으면 0 부터다', () => {
    expect(resumeFromMs(row(0, 1_000), null, 1_000 + 2_500, BEAT_MS)).toBe(0);
  });

  /**
   * 하트비트 추정은 실제보다 작을 수 있습니다 (첫 하트비트 전에 탭이 죽음 · 가려진 탭의 밀린 하트비트).
   * 그 앞에서 다시 돌리면 시선 구간이 겹쳐 종료 때 Take 전체 시선이 VALIDATION_FAILED 로 빠집니다.
   */
  it('이미 쌓인 기록보다 앞에서는 시작하지 않는다', () => {
    expect(resumeFromMs(row(0, 1_000), null, 1_000 + 2_500, BEAT_MS, 4_000)).toBe(4_000);
    expect(resumeFromMs(row(60_000, 1_000), null, 1_000 + 3_000, BEAT_MS, 90_000)).toBe(90_000);
    // 기록보다 뒤면 추정값 그대로다
    expect(resumeFromMs(row(60_000, 1_000), null, 1_000 + 3_000, BEAT_MS, 50_000)).toBe(63_000);
  });

  it('기록이 덮는 마지막 시각 — 시선 판정은 판정 주기만큼 뒤까지 덮는다', () => {
    expect(
      recordedUntilMs({
        gazeTMs: [1_000, 2_000, 3_000],
        gazeIntervalMs: 1_000,
        slideAtMs: [0, 2_500],
        coachAtMs: [3_500],
      }),
    ).toBe(4_000);
    expect(
      recordedUntilMs({ gazeTMs: [], gazeIntervalMs: 1_000, slideAtMs: [], coachAtMs: [] }),
    ).toBe(0);
  });
});

describe('슬라이드 이어받기', () => {
  it('가장 늦은 전환의 슬라이드와 그 시각이다', () => {
    const changes = [
      { atMs: 0, slideNumber: 1 },
      { atMs: 42_000, slideNumber: 3 },
      { atMs: 20_000, slideNumber: 2 },
    ];
    expect(lastSlide(changes)).toEqual({ slideNumber: 3, atMs: 42_000 });
  });

  it('기록이 없으면 null — 처음부터 시작한다', () => {
    expect(lastSlide([])).toBeNull();
  });
});

describe('코치 쿨다운 이어받기', () => {
  it('띄운 기록만 본다 — 참은 기록은 쿨다운을 걸지 않는다', () => {
    const history = coachHistory([
      { atMs: 10_000, type: 'SILENCE', fired: true },
      { atMs: 30_000, type: 'SILENCE', fired: true },
      { atMs: 40_000, type: 'LOW_VOLUME', fired: false },
      { atMs: 35_000, type: 'TIME_LEFT', fired: true },
    ]);
    expect(history).toEqual({
      lastFiredAt: 35_000,
      lastByType: { SILENCE: 30_000, TIME_LEFT: 35_000 },
    });
  });

  it('띄운 적이 없으면 간격 제한이 걸리지 않는다', () => {
    expect(coachHistory([]).lastFiredAt).toBe(-Infinity);
  });
});

describe('PresentationClock.start(fromMs)', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('이어받은 시점부터 잰다', () => {
    const now = vi.spyOn(performance, 'now').mockReturnValue(1_000);
    const clock = new PresentationClock();
    clock.start(63_400);
    expect(clock.elapsedMs()).toBe(63_400);

    now.mockReturnValue(3_000);
    expect(clock.elapsedMs()).toBe(65_400);
  });
});
