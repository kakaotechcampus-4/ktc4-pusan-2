import { describe, expect, it, vi } from 'vitest';
import {
  TYPING_MAX_LAG_MS,
  TYPING_TICK_MS,
  createTypingQueue,
  feedProgress,
  graphemes,
  type TypingItem,
} from './chatTyping';

/**
 * 시험이 돌리는 시계. `minDelay` 를 주면 그보다 짧은 타이머도 그만큼 늦게 부릅니다 — 숨은 탭에서 브라우저가
 * 타이머를 1초 넘게 늦추는 것을 흉내 냅니다
 */
class Clock {
  private minDelay: number;
  constructor(minDelay = 0) {
    this.minDelay = minDelay;
  }
  t = 0;
  now = () => this.t;
  private seq = 0;
  private timers = new Map<number, { at: number; fn: () => void }>();
  setTimeout = (fn: () => void, ms: number) => {
    const id = ++this.seq;
    this.timers.set(id, { at: this.t + Math.max(ms, this.minDelay), fn });
    return id;
  };
  clearTimeout = (handle: unknown) => {
    this.timers.delete(handle as number);
  };
  pending = () => this.timers.size;
  advance(ms: number) {
    const end = this.t + ms;
    for (;;) {
      const next = [...this.timers.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      if (!next || next[1].at > end) break;
      this.timers.delete(next[0]);
      this.t = next[1].at;
      next[1].fn();
    }
    this.t = end;
  }
}

const item = (index: number, text: string): TypingItem => ({ index, text });

function setup(reduced = false, minDelay = 0) {
  const clock = new Clock(minDelay);
  const done: number[] = [];
  const queue = createTypingQueue<TypingItem>({
    clock,
    reducedMotion: () => reduced,
    onLineDone: (line) => done.push(line.index),
  });
  const shown = () => queue.lines().map((l) => l.shown);
  return { clock, queue, done, shown };
}

describe('글자 나누기', () => {
  it('Intl.Segmenter 가 없으면 코드 포인트로 나눈다 — 완성형 한글은 그대로, 결합 문자 · 이모지 묶음은 조각난다', async () => {
    vi.resetModules();
    vi.stubGlobal('Intl', { ...Intl, Segmenter: undefined });
    try {
      const { graphemes: fallback } = await import('./chatTyping');
      expect(fallback('한글abc123')).toEqual(['한', '글', 'a', 'b', 'c', '1', '2', '3']);
      // 한계: 다 보이면 원문 그대로지만 효과 중에는 조각으로 보입니다(주석에 적어 둔 그대로)
      expect(fallback('👍🏽')).toHaveLength(2);
      expect(fallback('🇰🇷')).toHaveLength(2);
      expect(fallback('한'.normalize('NFD'))).toHaveLength(3);
      expect(fallback('👨‍👩‍👧').join('')).toBe('👨‍👩‍👧');
    } finally {
      vi.unstubAllGlobals();
      vi.resetModules();
    }
  });

  it('한글 · 옛 자모 · 이모지를 보이는 한 글자로 센다', () => {
    expect(graphemes('한글ᄒᆞᆫ👍🏽')).toEqual(['한', '글', 'ᄒᆞᆫ', '👍🏽']);
    expect(graphemes('')).toEqual([]);
  });
});

describe('글자 효과 (12-4 F12 · L3-41)', () => {
  it('약 60자/초로 보이고, 다 보인 문장만 done', () => {
    const { clock, queue, done } = setup();
    queue.push([item(0, '가'.repeat(60))]);
    expect(queue.lines()[0]).toMatchObject({ shown: '', done: false });
    clock.advance(500);
    expect(queue.lines()[0]!.shown.length).toBe(30);
    expect(done).toEqual([]);
    clock.advance(500);
    expect(queue.lines()[0]).toMatchObject({ done: true });
    expect(done).toEqual([0]);
    expect(clock.pending()).toBe(0);
  });

  it('문장은 받은 순서대로 하나씩 — 앞 문장이 다 보인 뒤에 뒤 문장이 시작한다', () => {
    const { clock, queue, shown, done } = setup();
    queue.push([item(0, '가나다'), item(1, '라마바')]);
    clock.advance(TYPING_TICK_MS);
    expect(shown()).toEqual(['가나다', '']);
    clock.advance(TYPING_TICK_MS);
    expect(shown()).toEqual(['가나다', '라마바']);
    expect(done).toEqual([0, 1]);
  });

  it('큐가 밀리면 빨라져 남은 글자를 약 2초 안에 다 보인다', () => {
    const { clock, queue } = setup();
    queue.push([item(0, '가'.repeat(1_000)), item(1, '나'.repeat(1_000))]);
    clock.advance(TYPING_MAX_LAG_MS + TYPING_TICK_MS * 2);
    expect(queue.lines().every((l) => l.done)).toBe(true);
  });

  it('틱이 1초씩 늦게 와도(숨은 탭) 받은 뒤 2초 안에 다 보인다 — 틱 횟수가 아니라 지난 시간으로 센다 (#956 리뷰)', () => {
    const { clock, queue, done } = setup(false, 1_000);
    queue.push([item(0, '가'.repeat(300)), item(1, '나'.repeat(300))]);
    clock.advance(TYPING_MAX_LAG_MS);
    expect(queue.lines().every((l) => l.done)).toBe(true);
    expect(done).toEqual([0, 1]);
  });

  it('늦은 틱에도 60자/초는 지킨다 — 짧은 답이 1초 늦게 깨면 그동안의 글자를 한꺼번에 보인다', () => {
    const { clock, queue } = setup(false, 1_000);
    queue.push([item(0, '가'.repeat(100))]);
    clock.advance(1_000);
    expect(queue.lines()[0]!.shown.length).toBeGreaterThanOrEqual(60);
  });

  it('남은 글자가 120자를 넘으면 2초 상한이 앞서 60자/초보다 빨라진다', () => {
    const { clock, queue } = setup();
    queue.push([item(0, '가'.repeat(300))]);
    clock.advance(1_000);
    // 60자/초면 60자지만, 2초 안에 300자를 다 보이려고 그보다 많이 보입니다
    expect(queue.lines()[0]!.shown.length).toBeGreaterThan(60);
    clock.advance(1_000);
    expect(queue.lines()[0]!.done).toBe(true);
  });

  it('알림(onLineDone) · 구독자가 던져도 큐는 멈추지 않는다 (#956 리뷰)', () => {
    const clock = new Clock();
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const queue = createTypingQueue<TypingItem>({
      clock,
      reducedMotion: () => false,
      onLineDone: (line) => {
        if (line.index === 0) throw new Error('화면 쪽 실수');
      },
    });
    queue.subscribe(() => {
      throw new Error('구독자 실수');
    });
    queue.push([item(0, '가나다'), item(1, '라마바사아')]);
    clock.advance(TYPING_MAX_LAG_MS);
    expect(queue.lines().map((l) => l.done)).toEqual([true, true]);
    expect(errors).toHaveBeenCalled();
    errors.mockRestore();
  });

  it('보인 글은 지우거나 되감지 않는다 — 같은 순번을 다시 넣어도 그대로', () => {
    const { clock, queue, shown } = setup();
    queue.push([item(0, '가나다라마바사')]);
    clock.advance(TYPING_TICK_MS);
    const before = shown()[0]!;
    queue.push([item(0, '다른 글')]);
    clock.advance(TYPING_TICK_MS);
    expect(shown()[0]!.startsWith(before)).toBe(true);
    expect(shown()).toHaveLength(1);
  });

  it('움직임 줄이기면 문장째 보인다', () => {
    const { clock, queue, done } = setup(true);
    queue.push([item(0, '가나다'), item(1, '라마')]);
    expect(queue.lines().map((l) => [l.shown, l.done])).toEqual([
      ['가나다', true],
      ['라마', true],
    ]);
    expect(done).toEqual([0, 1]);
    expect(clock.pending()).toBe(0);
  });

  it('효과 없이 넣으면 바로 보이고, 앞에서 보이던 문장을 먼저 다 보여 순서를 지킨다', () => {
    const { queue, shown, done } = setup();
    queue.push([item(0, '가나다라마바사')]);
    queue.push([item(1, '아자')], { animate: false });
    expect(shown()).toEqual(['가나다라마바사', '아자']);
    // 앞에서 보이던 새 문장은 알리고, 효과 없이 넣은 따라잡기 문장은 알리지 않습니다
    expect(done).toEqual([0]);
  });

  it('finish 는 남은 문장을 모두 보이고, reset 은 비우고, dispose 뒤에는 움직이지 않는다', () => {
    const { clock, queue, shown } = setup();
    queue.push([item(0, '가'.repeat(100))]);
    queue.finish();
    expect(shown()).toEqual(['가'.repeat(100)]);
    expect(clock.pending()).toBe(0);
    queue.reset();
    expect(queue.lines()).toEqual([]);
    // 비운 뒤에는 같은 순번을 다시 받습니다
    queue.push([item(0, '다시')]);
    queue.dispose();
    clock.advance(1_000);
    expect(shown()).toEqual(['']);
    expect(clock.pending()).toBe(0);
  });

  it('바뀔 때만 새 배열 · 구독자에게 알린다', () => {
    const { clock, queue } = setup();
    let calls = 0;
    const off = queue.subscribe(() => (calls += 1));
    const empty = queue.lines();
    expect(queue.lines()).toBe(empty);
    queue.push([item(0, '가나다')]);
    expect(calls).toBe(1);
    clock.advance(TYPING_TICK_MS);
    expect(calls).toBe(2);
    const settled = queue.lines();
    clock.advance(1_000);
    expect(queue.lines()).toBe(settled);
    off();
    queue.push([item(1, '라')]);
    expect(calls).toBe(2);
  });
});

describe('진행 신호 넣기', () => {
  it('처음부터 다시 보내면 비우고, 따라잡는 묶음은 효과 없이 보인다', () => {
    const { queue, shown } = setup();
    feedProgress(queue, { restart: true, catchUp: false, claims: [item(0, '가나다')] });
    expect(shown()).toEqual(['']);
    // 처음부터 다시 보낸 묶음은 앞에 그린 줄을 비우고 새로 받은 것으로 그립니다
    feedProgress(queue, {
      restart: true,
      catchUp: true,
      claims: [item(0, '근거 기록이 지워져 이 답을 가렸어요.'), item(1, '라마')],
    });
    expect(shown()).toEqual(['근거 기록이 지워져 이 답을 가렸어요.', '라마']);
  });
});

describe('점검 반영 — 알림 · 다시 그리기', () => {
  it('따라잡기 문장은 다 보여도 알리지 않는다 — 새로고침마다 지난 답을 읽지 않게 (L3-37)', () => {
    const { queue, done } = setup();
    queue.push([item(0, '옛 문장'), item(1, '옛 문장 둘')], { animate: false });
    expect(done).toEqual([]);
    expect(queue.lines().every((l) => l.done)).toBe(true);
  });

  it('움직임 줄이기로 바로 보인 새 문장은 알린다', () => {
    const { queue, done } = setup(true);
    queue.push([item(0, '새 문장')]);
    expect(done).toEqual([0]);
  });

  it('다 보인 줄은 같은 객체 그대로 — 매 틱 모든 문장을 다시 그리지 않게', () => {
    const { clock, queue } = setup();
    queue.push([item(0, '가나'), item(1, '다'.repeat(30))]);
    clock.advance(TYPING_TICK_MS);
    const first = queue.lines()[0]!;
    expect(first.done).toBe(true);
    clock.advance(TYPING_TICK_MS);
    expect(queue.lines()[0]).toBe(first);
    expect(queue.lines()[1]!.done).toBe(false);
  });
});
