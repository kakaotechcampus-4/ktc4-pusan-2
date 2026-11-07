import type { ChatClock, TransportSignal } from './chatTransport';

/**
 * 글자 효과 (`chat-agent.md` 12-4 F12 · L3-41) — 받은 답 문장을 큐에 넣어 글자 단위로 보입니다.
 *
 * - 약 60자/초. 큐가 밀리면 빨라져서, 마지막으로 받은 뒤 약 2초 안에 남은 글자를 다 보입니다.
 *   **2초 상한이 앞섭니다** — 남은 글자가 120자(60자/초 × 2초)를 넘으면 60자/초보다 빨라집니다
 *   (150자면 약 75자/초, 1,000자면 약 500자/초). 2초는 FE 가 정한 값입니다(문서 L3-41 은 "밀리면 빨라짐"만).
 * - 시간은 틱 횟수가 아니라 **실제로 지난 시간**(`performance.now()`)으로 셉니다 — 숨은 탭에서 브라우저가 타이머를
 *   1초 넘게 늦춰도, 다시 깨어난 틱이 지난 시간만큼 한꺼번에 보이고 2초가 지났으면 남은 글자를 다 보입니다(#956 리뷰).
 * - 글자는 한글 결합 문자 · 이모지가 깨지지 않게 "보이는 한 글자"(grapheme) 단위로 셉니다(`Intl.Segmenter`).
 *   없는 환경의 한계는 `graphemes` 에 적었습니다.
 * - `prefers-reduced-motion` 이면 문장째 보입니다. 새로고침 · 다시 열기 · 지난 메시지도 효과 없이 그립니다.
 * - 문장은 받은 순서대로 하나씩 보이고, 보인 글은 지우거나 되감지 않습니다(낸 문장은 최종 답의 앞부분).
 * - 근거 칩은 문장이 다 보인 뒤에 답니다 — 화면은 `done` 인 줄에만 칩을 그립니다.
 * - 실행이 끝나 최종 답을 받으면 `finish()` 로 큐를 비우고 최종 답으로 맞춥니다.
 *
 * 화면과 상관없는 순수 큐라, 시계를 바꿔 끼워 시험합니다.
 */

/** 기본 속도 — 약 60자/초 */
export const TYPING_CHARS_PER_SEC = 60;
/** 한 번에 그리는 간격. 너무 잘게 그리면 화면이 바빠집니다 */
export const TYPING_TICK_MS = 50;
/** 큐가 밀려도 남은 글자는 이 시간 안에 다 보입니다 */
export const TYPING_MAX_LAG_MS = 2_000;

type Segmenter = { segment: (text: string) => Iterable<{ segment: string }> };
type SegmenterCtor = new (locale: string, options: { granularity: 'grapheme' }) => Segmenter;

/**
 * 보이는 한 글자씩 — "한" · 옛 자모 "ᄒᆞᆫ" · "👍🏽" 가 각각 한 글자입니다(`Intl.Segmenter`).
 *
 * `Intl.Segmenter` 가 없는 환경(예: Firefox 125 전)에서는 코드 포인트로 나눕니다. 완성형 한글 · 영문 · 숫자는 그래도
 * 깨지지 않지만, **결합 문자 · 이모지 묶음은 효과 중에 조각으로 보입니다** — 자모로 쓴(NFD) "한" 은 3조각,
 * 👍🏽 · 🇰🇷 은 2조각, 👨‍👩‍👧 은 5조각. 다 보이면 원문 그대로라 끝난 글은 같습니다(#956 리뷰)
 */
export function graphemes(text: string): string[] {
  if (segmenter === undefined) {
    const Ctor = (Intl as unknown as { Segmenter?: SegmenterCtor }).Segmenter;
    segmenter = Ctor ? new Ctor('ko', { granularity: 'grapheme' }) : null;
  }
  if (!segmenter) return Array.from(text);
  return Array.from(segmenter.segment(text), (s) => s.segment);
}

/** 한 번만 만들어 다시 씁니다 — 없는 환경이면 null */
let segmenter: Segmenter | null | undefined;

/** 큐에 넣는 문장 — 실행 안 순번과 글 */
export interface TypingItem {
  index: number;
  text: string;
}

/** 화면이 그리는 줄 — `shown` 까지 보이고, `done` 이면 다 보여 칩을 답니다 */
export interface TypedLine<T extends TypingItem> {
  index: number;
  item: T;
  shown: string;
  done: boolean;
}

export interface TypingQueue<T extends TypingItem> {
  /**
   * 문장을 넣습니다 — 같은 순번은 한 번만. `animate: false` 면 효과 없이 바로 보입니다
   * (그 앞에서 보이던 문장도 먼저 다 보여 순서를 지킵니다)
   */
  push: (items: readonly T[], options?: { animate?: boolean }) => void;
  /** 남은 문장을 모두 보입니다 — 실행이 끝나 최종 답으로 맞출 때 */
  finish: () => void;
  /** 비웁니다 — 같은 실행의 문장을 처음부터 다시 받을 때(`restart`) · 다른 실행으로 바꿀 때 */
  reset: () => void;
  /** 지금 그릴 줄. 바뀔 때만 새 배열입니다(`useSyncExternalStore` 에 그대로 씁니다) */
  lines: () => readonly TypedLine<T>[];
  /** 바뀌면 부릅니다 */
  subscribe: (listener: () => void) => () => void;
  dispose: () => void;
}

export interface TypingOptions<T extends TypingItem> {
  /** `now` 는 지난 시간을 재는 데 씁니다 — 브라우저는 `performance.now()` */
  clock?: Pick<ChatClock, 'setTimeout' | 'clearTimeout' | 'now'>;
  /** 움직임 줄이기 — 켜져 있으면 문장째 보입니다. 넣을 때마다 다시 읽습니다 */
  reducedMotion?: () => boolean;
  /**
   * 새 문장이 다 보였을 때 — 화면이 문장 단위로 읽어 줄 때 씁니다(글자마다가 아니라, L3-37).
   * 효과 없이 넣은 따라잡기 문장(`animate: false`)은 이미 있던 글이라 부르지 않습니다 — 새로고침할 때마다
   * 지난 답 전체를 읽지 않게. 움직임 줄이기로 바로 보인 새 문장은 부릅니다
   */
  onLineDone?: (line: TypedLine<T>) => void;
}

const browserClock: Pick<ChatClock, 'setTimeout' | 'clearTimeout' | 'now'> = {
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>),
  now: () => globalThis.performance.now(),
};

/** 운영체제의 '움직임 줄이기' — 물을 수 없는 환경(시험 · 서버)에서는 꺼진 것으로 봅니다 */
export const prefersReducedMotion = () =>
  typeof globalThis.matchMedia === 'function' &&
  globalThis.matchMedia('(prefers-reduced-motion: reduce)').matches;

export function createTypingQueue<T extends TypingItem>(
  options: TypingOptions<T> = {},
): TypingQueue<T> {
  const clock = options.clock ?? browserClock;
  const reducedMotion = options.reducedMotion ?? prefersReducedMotion;
  type Entry = { item: T; parts: string[]; count: number; line: TypedLine<T> };
  const entries: Entry[] = [];
  const seen = new Set<number>();
  const listeners = new Set<() => void>();
  let timer: unknown = null;
  /** 남은 글자를 다 보여야 하는 때 — 새 문장을 받을 때마다 지금부터 다시 2초 */
  let deadline = 0;
  /** 마지막으로 글자를 보인 때 — 이번 틱까지 지난 시간을 잽니다 */
  let lastTick = 0;
  let snapshot: readonly TypedLine<T>[] = [];
  let disposed = false;

  const lineOf = (e: Pick<Entry, 'item' | 'parts' | 'count'>): TypedLine<T> => ({
    index: e.item.index,
    item: e.item,
    shown: e.parts.slice(0, e.count).join(''),
    done: e.count >= e.parts.length,
  });

  /**
   * 바뀐 줄만 새 객체로 — 다 보인 줄은 같은 객체라 화면이 매 틱 모든 문장을 다시 그리지 않습니다.
   * `announce` 는 다 보여 알릴 새 문장입니다
   */
  const changed = (touched: Entry[], announce: Entry[]) => {
    for (const e of touched) e.line = lineOf(e);
    snapshot = entries.map((e) => e.line);
    // 화면 쪽 콜백 하나가 던져도 다른 알림 · 다음 틱은 이어집니다 — 답 표시가 얼지 않게(#956 리뷰)
    for (const e of announce) safely(() => options.onLineDone?.(e.line));
    for (const listener of listeners) safely(listener);
  };

  const safely = (fn: () => void) => {
    try {
      fn();
    } catch (error) {
      console.error('글자 효과 알림을 처리하지 못했어요', error);
    }
  };

  const remaining = () => entries.reduce((sum, e) => sum + e.parts.length - e.count, 0);

  const stop = () => {
    if (timer !== null) clock.clearTimeout(timer);
    timer = null;
  };

  /** 앞 문장부터 차례로, 이번 틱에 보일 만큼 */
  const tick = () => {
    timer = null;
    if (disposed) return;
    const now = clock.now();
    const elapsed = Math.max(now - lastTick, TYPING_TICK_MS);
    lastTick = now;
    const left = remaining();
    // 지난 시간만큼 60자/초로, 그리고 마감까지 남은 시간에 남은 글자를 고르게 나눈 몫 중 큰 쪽 — 마감이 지났으면 전부.
    // 틱이 늦게 와도(숨은 탭) 늦은 만큼 한꺼번에 보여 2초를 지킵니다
    let budget =
      now >= deadline
        ? left
        : Math.max(
            Math.round((elapsed * TYPING_CHARS_PER_SEC) / 1000),
            Math.ceil((left * elapsed) / (deadline - now + elapsed)),
          );
    const touched: Entry[] = [];
    const finished: Entry[] = [];
    for (const e of entries) {
      if (budget <= 0) break;
      const left = e.parts.length - e.count;
      if (left <= 0) continue;
      const step = Math.min(left, budget);
      e.count += step;
      budget -= step;
      touched.push(e);
      if (e.count >= e.parts.length) finished.push(e);
    }
    // 다음 틱을 먼저 겁니다 — 알림 쪽에서 무슨 일이 나도 남은 글자가 멈추지 않게
    schedule();
    changed(touched, finished);
  };

  const schedule = () => {
    if (disposed || timer !== null || remaining() === 0) return;
    timer = clock.setTimeout(tick, TYPING_TICK_MS);
  };

  /** 남은 글자를 모두 보입니다 */
  const showAll = (): Entry[] => {
    stop();
    const finished = entries.filter((e) => e.count < e.parts.length);
    for (const e of finished) e.count = e.parts.length;
    return finished;
  };

  return {
    push(items, { animate = true } = {}) {
      if (disposed) return;
      const fresh = items.filter((item) => !seen.has(item.index));
      if (fresh.length === 0) return;
      const instant = !animate || reducedMotion();
      const now = clock.now();
      deadline = now + TYPING_MAX_LAG_MS;
      // 쉬고 있었으면 지금부터 잽니다 — 쉰 시간이 첫 틱에 한꺼번에 몰리지 않게
      if (timer === null) lastTick = now;
      // 효과 없이 보일 때는 앞에서 보이던 문장부터 다 보여 순서를 지킵니다 — 그 문장들은 새 글이라 알립니다
      const flushed = instant ? showAll() : [];
      const added: Entry[] = [];
      for (const item of fresh) {
        seen.add(item.index);
        const parts = graphemes(item.text);
        const count = instant ? parts.length : 0;
        const entry: Entry = { item, parts, count, line: lineOf({ item, parts, count }) };
        entries.push(entry);
        added.push(entry);
      }
      // 움직임 줄이기로 바로 보인 새 문장은 알리고, 따라잡기(`animate: false`)는 알리지 않습니다
      const announce = [...flushed, ...(instant && animate ? added : [])];
      schedule();
      changed([...flushed, ...added], announce);
    },
    finish() {
      if (disposed) return;
      const finished = showAll();
      if (finished.length > 0) changed(finished, finished);
    },
    reset() {
      stop();
      entries.length = 0;
      seen.clear();
      if (snapshot.length > 0) changed([], []);
    },
    lines: () => snapshot,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    dispose() {
      disposed = true;
      stop();
      listeners.clear();
    },
  };
}

type ProgressSignal = Extract<TransportSignal, { type: 'progress' }>;

/**
 * 진행 신호를 큐에 넣습니다 — 처음부터 다시 보내면(`restart`) 비우고, 이미 만들어진 문장을 따라잡는 묶음이면
 * (`catchUp`: 새로고침 · 다시 열기 · 다른 탭이 시작한 실행) 효과 없이 보입니다.
 *
 * **큐는 실행마다 하나**입니다 — 새 실행의 첫 진행 신호도 `restart` 라 큐를 비웁니다. 끝난 답은 메시지 목록으로
 * 그리므로, 화면은 지금 실행의 답만 이 큐로 그리고 끝나면(`ended`) `finish()` 뒤 메시지 목록으로 바꿉니다
 */
export function feedProgress<T extends TypingItem>(
  queue: TypingQueue<T>,
  signal: Pick<ProgressSignal, 'restart' | 'catchUp'> & { claims: readonly T[] },
) {
  if (signal.restart) queue.reset();
  queue.push(signal.claims, { animate: !signal.catchUp });
}
