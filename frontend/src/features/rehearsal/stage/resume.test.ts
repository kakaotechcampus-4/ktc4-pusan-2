import { afterEach, describe, expect, it, vi } from 'vitest';
import { PresentationClock } from '@/shared/lib/clock';
import {
  abandonOutcome,
  lastSlide,
  mergeSlideChanges,
  openingSlides,
  leftWindow,
  LIVE_BEAT_MS,
  recordedUntilMs,
  resumeFromMs,
  saveMetrics,
  saveSlides,
  takeMetrics,
  takeSlides,
} from './resume';

const BEAT_MS = 5_000;
const row = (elapsedMs: number, lastBeatAt: number) => ({
  clientSessionId: 's-1',
  elapsedMs,
  lastBeatAt,
});

describe('이 창에서 무대를 이어 열어도 되나', () => {
  const NOW = 1_000_000;
  const running = (lastBeatAt: number) => ({
    status: 'RUNNING',
    ending: null,
    submittedAt: null,
    lastBeatAt,
  });

  /** 라우터 state 는 새로고침해도 · 닫은 탭을 복원해도 남습니다 */
  it('같은 탭이면 이어 간다 — 새로고침이다', () => {
    expect(leftWindow(running(NOW - 60_000), true, NOW)).toBeNull();
  });

  it('다른 창에서 열었는데 하트비트가 멈췄으면 창을 닫았다 다시 연 것이다', () => {
    expect(leftWindow(running(NOW - LIVE_BEAT_MS), false, NOW)).toBe('CLOSED');
  });

  /** 그 창이 같은 세션 행을 쓰므로 아무것도 바꾸면 안 됩니다 */
  it('다른 창에서 열었는데 하트비트가 최근이면 그 창이 아직 발표 중이다', () => {
    expect(leftWindow(running(NOW - 3_000), false, NOW)).toBe('OTHER_WINDOW');
  });

  /** 끝내기를 누른 뒤의 행은 무대를 열지 않고 종료를 마저 보내는 길이 따로 있습니다 */
  it('끝내기를 누른 뒤의 행은 막지 않는다', () => {
    const ending = { durationMs: 60_000, endedAtIso: '2026-10-23T00:00:00Z' };
    expect(leftWindow({ ...running(0), ending }, false, NOW)).toBeNull();
    expect(leftWindow({ ...running(0), submittedAt: NOW - 1 }, false, NOW)).toBeNull();
    expect(leftWindow({ ...running(0), status: 'ENDED' }, false, NOW)).toBeNull();
  });

  /** 다른 브라우저에서 열었거나 사이트 데이터를 지웠습니다. 처음부터 열면 서버의 Take 시간대와 겹칩니다 */
  it('다른 창에서 열었는데 이 브라우저에 기록이 없으면 이어 가지 않는다', () => {
    expect(leftWindow(null, false, NOW)).toBe('CLOSED');
    // 준비 화면에서 막 넘어왔는데 행이 없으면(그사이 데이터를 지움) 그 값으로 이어 씁니다
    expect(leftWindow(null, true, NOW)).toBeNull();
  });

  /** 안내 화면을 본 뒤 새로고침했습니다. 종료를 마저 보내는 길로 빠지면 안 됩니다 */
  it('창을 닫아 이미 끝낸 행은 같은 탭이어도 다시 열지 않는다', () => {
    expect(leftWindow({ ...running(NOW), status: 'ABORTED' }, true, NOW)).toBe('CLOSED');
  });

  /** ending · submittedAt 이 생기기 전에 만든 행에는 키가 없습니다 */
  it('옛 행에 없는 필드는 없음으로 읽는다', () => {
    const legacy = { status: 'RUNNING', lastBeatAt: NOW - 60_000 };
    expect(leftWindow(legacy, false, NOW)).toBe('CLOSED');
  });
});

describe('창을 닫은 Take 를 서버에 끝내 달라고 한 뒤', () => {
  it('실패로 바뀌었으면 실패 화면이다', () => {
    expect(abandonOutcome('FAILED', null)).toBe('CLOSED');
  });

  it('다른 창이 발표 중이라고 거절하면 그 창에 맡긴다', () => {
    expect(abandonOutcome(null, 'TAKE_IN_PROGRESS')).toBe('OTHER_WINDOW');
  });

  it('끝내기가 이미 닿았으면 분석 중 화면이다', () => {
    expect(abandonOutcome('ANALYZING', null)).toBe('PROCESSING');
    expect(abandonOutcome('COMPLETED', null)).toBe('PROCESSING');
  });

  /** 서버는 떠난 Take 타이머가 정리합니다 */
  it('알리지 못했어도 실패 화면이다', () => {
    expect(abandonOutcome(null, 'REQUEST_FAILED')).toBe('CLOSED');
    expect(abandonOutcome(null, null)).toBe('CLOSED');
  });
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

  it('기록이 덮는 마지막 시각 — 시선 1초 기록은 자기 길이만큼 뒤까지 덮는다', () => {
    const s = (t_ms: number) => ({ t_ms, duration_ms: 1_000 });
    expect(
      recordedUntilMs({
        gaze: [s(1_000), s(2_000), s(3_000)],
        slideAtMs: [0, 2_500],
      }),
    ).toBe(4_000);
    // 슬라이드 전환이 더 늦으면 그 시각입니다
    expect(recordedUntilMs({ gaze: [s(1_000)], slideAtMs: [4_500] })).toBe(4_500);
    expect(recordedUntilMs({ gaze: [], slideAtMs: [] })).toBe(0);
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

describe('새로고침 직전에 못 보낸 metrics', () => {
  /** 테스트는 Node 에서 돌아 sessionStorage 가 없습니다. Map 으로 흉내 냅니다 */
  function stubStorage() {
    const store = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => store.set(k, v),
      removeItem: (k: string) => store.delete(k),
    });
    return store;
  }

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('적어 둔 것을 한 번만 꺼낸다', () => {
    stubStorage();
    saveMetrics('t1', { clientSessionId: 's-1', messages: ['a', 'b'] });
    expect(takeMetrics('t1', 's-1')).toEqual(['a', 'b']);
    expect(takeMetrics('t1', 's-1')).toEqual([]);
  });

  it('다른 세션이 적은 것은 버린다', () => {
    const store = stubStorage();
    saveMetrics('t1', { clientSessionId: 's-old', messages: ['a'] });
    expect(takeMetrics('t1', 's-1')).toEqual([]);
    expect(store.size).toBe(0);
  });

  it('보낼 것이 없으면 지난 값도 지운다', () => {
    const store = stubStorage();
    saveMetrics('t1', { clientSessionId: 's-1', messages: ['a'] });
    saveMetrics('t1', { clientSessionId: 's-1', messages: [] });
    expect(store.size).toBe(0);
  });

  it('저장소를 못 쓰면 던지지 않고 빈 목록이다', () => {
    vi.stubGlobal('sessionStorage', undefined);
    expect(() => saveMetrics('t1', { clientSessionId: 's-1', messages: ['a'] })).not.toThrow();
    expect(takeMetrics('t1', 's-1')).toEqual([]);
  });
});

describe('openingSlides', () => {
  it('처음 시작이면 지금 장을 Take 시작(0)으로 보낸다', () => {
    expect(openingSlides([], 1)).toEqual([{ type: 'slide', slide_number: 1, t_ms: 0 }]);
  });

  it('이어받았으면 장 넘김 기록 전체를 시각 순서대로 다시 보낸다 — 끊긴 동안 넘긴 장이 빠지지 않게', () => {
    const history = [
      { atMs: 80_000, slideNumber: 4 },
      { atMs: 0, slideNumber: 1 },
      { atMs: 70_000, slideNumber: 3 },
      { atMs: 30_000, slideNumber: 2 },
    ];
    expect(openingSlides(history, 4).map((m) => [m.slide_number, m.t_ms])).toEqual([
      [1, 0],
      [2, 30_000],
      [3, 70_000],
      [4, 80_000],
    ]);
  });
});

describe('새로고침 직전에 넘긴 장 (saveSlides · mergeSlideChanges)', () => {
  function stubStorage() {
    const store = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => store.set(k, v),
      removeItem: (k: string) => store.delete(k),
    });
    return store;
  }

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const change = (atMs: number, slideNumber: number) => ({ atMs, slideNumber });

  it('적어 둔 것을 한 번만 꺼내고, 다른 세션이 적은 것은 버린다', () => {
    stubStorage();
    saveSlides('t1', { clientSessionId: 's-1', slides: [change(0, 1), change(42_300, 3)] });
    expect(takeSlides('t1', 's-2')).toEqual([]);

    saveSlides('t1', { clientSessionId: 's-1', slides: [change(0, 1), change(42_300, 3)] });
    expect(takeSlides('t1', 's-1')).toEqual([change(0, 1), change(42_300, 3)]);
    expect(takeSlides('t1', 's-1')).toEqual([]);
  });

  it('모양이 틀린 행은 버리고, 저장소를 못 쓰면 빈 목록이다', () => {
    const store = stubStorage();
    store.set(
      'rehearsal:slides:t1',
      JSON.stringify({ clientSessionId: 's-1', slides: [change(0, 1), { atMs: 'x' }, null] }),
    );
    expect(takeSlides('t1', 's-1')).toEqual([change(0, 1)]);

    vi.stubGlobal('sessionStorage', undefined);
    expect(() =>
      saveSlides('t1', { clientSessionId: 's-1', slides: [change(0, 1)] }),
    ).not.toThrow();
    expect(takeSlides('t1', 's-1')).toEqual([]);
  });

  it('IndexedDB 에 아직 없던 마지막 장도 합쳐서 이어받는다 — 끊긴 채로 넘기고 바로 새로고침', () => {
    // 42.3초에 3번으로 넘겼지만 IndexedDB 쓰기가 끝나기 전에 탭이 사라졌습니다
    const stored = [change(0, 1), change(30_000, 2)];
    const saved = [change(0, 1), change(30_000, 2), change(42_300, 3)];
    const merged = mergeSlideChanges(stored, saved);

    expect(merged).toEqual([change(0, 1), change(30_000, 2), change(42_300, 3)]);
    // 되살릴 장도 3번입니다. IndexedDB 만 보면 2번으로 돌아갑니다
    expect(lastSlide(merged)).toEqual({ slideNumber: 3, atMs: 42_300 });
    expect(openingSlides(merged, 3).map((m) => m.t_ms)).toEqual([0, 30_000, 42_300]);
  });

  it('같은 시각은 하나로 두고 시각 순서로 돌려준다', () => {
    expect(mergeSlideChanges([change(30_000, 2), change(0, 1)], [change(30_000, 9)])).toEqual([
      change(0, 1),
      change(30_000, 2),
    ]);
  });
});
