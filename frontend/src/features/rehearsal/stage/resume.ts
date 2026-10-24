import type { SlideMessage } from '../lib/sttProtocol';
import type { HandedMetrics } from '../lib/sttSocket';
import type { Ms, TakeStatus } from '@/types/api';

/**
 * 새로고침한 Take 를 이어받는 계산. **React 와 IndexedDB 를 모릅니다** — 값만 받아 값을 돌려줍니다.
 *
 * 새로고침은 발표가 잠깐 멈췄다가 이어지는 것으로 다룹니다. 무대 시계를 0 부터 다시 돌리면
 * 시선·슬라이드·코치·전사 기록이 새로고침 전 기록과 시간대가 겹치고, 종료 때 `durationMs` 가
 * 새로고침 뒤만 남습니다. 그래서 시계를 멈춘 시점부터 다시 돌리고, 슬라이드와 코치도 이어받습니다.
 *
 * 전사는 여기서 다루지 않습니다. 소켓은 새로 생겨 `seq` 를 1 부터 다시 세고, `offset_ms` 는
 * 이어받은 시계를 따라 이어집니다. 서버는 새 연결의 첫 프레임이 뒤로 간 것을 보고 새 구간으로 받습니다.
 */

/** 새로고침 직전 무대 시계. 탭이 닫히는 순간(`pagehide`) 동기로 적습니다 */
export interface SavedClock {
  clientSessionId: string;
  elapsedMs: Ms;
}

const clockKey = (takeId: string) => `rehearsal:clock:${takeId}`;

/**
 * `sessionStorage` 인 이유 — `pagehide` 에서 IndexedDB 쓰기는 끝나기 전에 탭이 사라질 수 있습니다.
 * 이건 동기라 새로고침이면 남고, 탭이 바뀌면 사라집니다(다른 창에서는 이어 가지 않습니다 — `leftWindow`).
 */
export function saveClock(takeId: string, clock: SavedClock): void {
  try {
    sessionStorage.setItem(clockKey(takeId), JSON.stringify(clock));
  } catch {
    // 사생활 보호 모드 · 저장 공간 부족. 하트비트로 이어받습니다
  }
}

export function readClock(takeId: string): SavedClock | null {
  try {
    const raw = sessionStorage.getItem(clockKey(takeId));
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<SavedClock>;
    if (typeof value.clientSessionId !== 'string' || typeof value.elapsedMs !== 'number') {
      return null;
    }
    return { clientSessionId: value.clientSessionId, elapsedMs: value.elapsedMs };
  } catch {
    return null;
  }
}

export function clearClock(takeId: string): void {
  try {
    sessionStorage.removeItem(clockKey(takeId));
  } catch {
    // 지우지 못해도 다음 Take 는 takeId 가 달라 읽지 않습니다
  }
}

/**
 * 새로고침 직전에 서버로 못 보낸 1초 측정값(`metrics`, JSON 문자열). 탭이 닫히는 순간(`pagehide`) 적습니다.
 *
 * 두 가지가 들어갑니다 — 시선을 기다리느라 아직 만들지 않은 마지막 몇 초(`MetricsClock.flush`)와,
 * 끊겨 있어 소켓이 들고 있던 것(`SttSocket.handOffMetrics`). 메모리에만 있어 새로고침하면 사라지고,
 * 새 페이지는 저장한 시계부터 다시 보내므로 그 구간을 보낼 길이 없습니다. 음량 1초 기록도
 * 메모리에만 있어 IndexedDB 에서 다시 만들 수 없습니다. 새 소켓이 붙으면 이것부터 보냅니다.
 *
 * 같은 1초가 두 번 가도 BE 는 먼저 받은 것을 둡니다 (`architecture.md` 6-2).
 */
export interface SavedMetrics {
  clientSessionId: string;
  messages: HandedMetrics[];
}

const metricsKey = (takeId: string) => `rehearsal:metrics:${takeId}`;

export function saveMetrics(takeId: string, saved: SavedMetrics): void {
  try {
    if (saved.messages.length === 0) sessionStorage.removeItem(metricsKey(takeId));
    else sessionStorage.setItem(metricsKey(takeId), JSON.stringify(saved));
  } catch {
    // 사생활 보호 모드 · 저장 공간 부족. 그 몇 초는 잃습니다
  }
}

/**
 * 적어 둔 것을 꺼내고 지웁니다. 다른 세션이 적은 것 · 모양이 틀린 행은 버립니다 — 시각을 모르면
 * 순서를 맞춰 보낼 수 없습니다. 저장소에서 온 값은 여기서만 확인합니다 (소켓은 그대로 씁니다)
 */
export function takeMetrics(takeId: string, clientSessionId: string): HandedMetrics[] {
  try {
    const raw = sessionStorage.getItem(metricsKey(takeId));
    sessionStorage.removeItem(metricsKey(takeId));
    if (!raw) return [];
    const value = JSON.parse(raw) as Partial<SavedMetrics>;
    if (value.clientSessionId !== clientSessionId || !Array.isArray(value.messages)) return [];
    return value.messages.filter(
      (m): m is HandedMetrics => typeof m?.tMs === 'number' && typeof m?.data === 'string',
    );
  } catch {
    return [];
  }
}

export function clearMetrics(takeId: string): void {
  try {
    sessionStorage.removeItem(metricsKey(takeId));
  } catch {
    // 지우지 못해도 다음 Take 는 takeId 가 달라 읽지 않습니다
  }
}

/**
 * 다른 창이 아직 발표 중이라고 보는 하트비트 간격. 하트비트는 5초마다라 두 번 놓칠 만큼 둡니다.
 * BE 가 발표 중으로 보는 오디오 간격(`LIVE_AUDIO_SEC`)과 같습니다
 */
export const LIVE_BEAT_MS = 10_000;

/**
 * 이 창에서 무대를 열면 안 되는 이유. `CLOSED` 창을 닫았다 다시 열었습니다 — 이어 가지 않고 실패로
 * 끝냅니다. `OTHER_WINDOW` 다른 창이 아직 발표 중입니다 — 아무것도 바꾸지 않습니다.
 */
export type LeftWindow = 'CLOSED' | 'OTHER_WINDOW';

/**
 * 리허설 주소를 열었을 때 무대를 이어 열어도 되나. 열면 안 되면 그 이유, 되면 null 입니다.
 *
 * **같은 탭인지는 라우터 state 로 압니다.** 장치 점검이 `clientSessionId` 를 실어 보내고, 이 값은
 * 새로고침해도 · 닫은 탭을 복원해도(Ctrl+Shift+T) 남습니다. 주소를 새 창에 직접 열면 비어 있습니다.
 * 같은 탭이면 새로고침이라 이어 갑니다 — 닫은 탭을 늦게 복원했으면 서버가 첫 연결을
 * `TAKE_ENDED`(`CLOSED`)로 거절하고, 그때 실패 화면으로 갑니다.
 *
 * 다른 창에서 열었으면 이어 가지 않습니다. 하트비트가 최근이면 그 창이 아직 발표 중이고, 아니면
 * 창을 닫았다 다시 연 것입니다. 이 브라우저에 세션 행이 없어도(다른 브라우저 · 사이트 데이터를 지움)
 * 이어 가지 않습니다 — 처음부터 열면 서버에 이미 쌓인 Take 시간대와 겹칩니다.
 * 창을 닫아 이미 끝낸 행(`ABORTED`)은 어느 탭이든 다시 열지 않습니다.
 * 끝내기를 누른 뒤의 행(`ending` · 제출함 · 끝남)은 여기서 막지 않습니다 — 무대를 열지 않고 종료를
 * 마저 보내는 길이 따로 있습니다.
 *
 * `ending` · `submittedAt` 은 나중에 생긴 필드라 옛 행에는 키가 없습니다. 없음으로 읽습니다.
 */
export function leftWindow(
  row: {
    status: string;
    ending?: unknown;
    submittedAt?: number | null;
    lastBeatAt: number;
  } | null,
  sameTab: boolean,
  now: number,
): LeftWindow | null {
  if (row?.status === 'ABORTED') return 'CLOSED';
  if (sameTab) return null;
  if (!row) return 'CLOSED';
  const ended = (row.ending ?? null) !== null || (row.submittedAt ?? null) !== null;
  if (row.status !== 'RUNNING' || ended) return null;
  return now - row.lastBeatAt < LIVE_BEAT_MS ? 'OTHER_WINDOW' : 'CLOSED';
}

/** 창을 닫은 Take 를 서버에 끝내 달라고 한 뒤 어디로 갈지 */
export type AbandonOutcome = 'CLOSED' | 'OTHER_WINDOW' | 'PROCESSING';

/**
 * abandon 응답의 상태(`status`)나 실패 코드(`errorCode`)로 어디로 갈지 정합니다.
 *
 * - 409 `TAKE_IN_PROGRESS` — 다른 창이 아직 발표 중입니다 (서버가 오디오가 들어오는 걸 봤습니다)
 * - `ANALYZING` · `COMPLETED` — 끝내기가 이미 서버에 닿았습니다. 실패가 아니라 분석 중입니다
 * - 그 밖(실패로 바뀜 · 알리지 못함) — 실패 화면. 못 알렸으면 서버의 떠난 Take 타이머가 정리합니다
 */
export function abandonOutcome(
  status: TakeStatus | null,
  errorCode: string | null,
): AbandonOutcome {
  if (errorCode === 'TAKE_IN_PROGRESS') return 'OTHER_WINDOW';
  if (status === 'ANALYZING' || status === 'COMPLETED') return 'PROCESSING';
  return 'CLOSED';
}

/**
 * 무대 시계를 어디서부터 다시 돌릴까.
 *
 * 1. 새로고침이면 `pagehide` 에 적어 둔 값이 있습니다. 정확합니다.
 * 2. 없으면(탭이 죽은 뒤 복원했다 · `pagehide` 에 못 적었다) 마지막 하트비트에서 추정합니다 —
 *    `elapsedMs + min(지금 − lastBeatAt, beatMs)`. 탭이 죽은 건 마지막 하트비트 뒤
 *    한 주기 안이라 대개 이전 탭이 보낸 시각보다 뒤입니다. 가려진 탭은 브라우저가 타이머를
 *    늦춰서 앞설 수도 있는데, 그래도 전사는 서버가 offset 이 뒤로 간 것을 보고 새 구간으로 받습니다.
 *    상한을 두는 이유는 다음 날 같은 주소로 들어와도 시간이 하루만큼 튀지 않게 하려는 것입니다.
 *    `elapsedMs` 가 0 이면 0 부터입니다 — 준비 화면에서 막 넘어온 행도 그렇고(`lastBeatAt` 이
 *    Take 를 만든 시각이라 그대로 더하면 처음부터 몇 초가 비어 시작합니다), 첫 하트비트 전에
 *    탭이 죽었으면 앞 5초가 겹칠 뿐입니다.
 *
 * 적어 둔 값과 하트비트 중 큰 쪽을 씁니다. 적어 둔 값이 더 작을 일은 없지만, 작으면 기록이 겹칩니다.
 *
 * 마지막으로 **이미 쌓인 기록이 덮는 시각(`recordedUntil`)보다 앞에서는 시작하지 않습니다.**
 * 하트비트 추정은 실제보다 작을 수 있습니다 — 첫 하트비트 전에 탭이 죽었거나(0 부터가 됨),
 * 가려진 탭이라 하트비트가 밀렸던 경우. 그대로 돌리면 시선 구간이 앞 기록과 겹치고, 종료 때
 * 검증에 걸려 Take 전체 시선이 VALIDATION_FAILED 로 빠집니다.
 */
export function resumeFromMs(
  row: { clientSessionId: string; elapsedMs: Ms; lastBeatAt: number },
  saved: SavedClock | null,
  now: number,
  beatMs: Ms,
  recordedUntil: Ms = 0,
): Ms {
  return Math.max(estimate(row, saved, now, beatMs), recordedUntil);
}

function estimate(
  row: { clientSessionId: string; elapsedMs: Ms; lastBeatAt: number },
  saved: SavedClock | null,
  now: number,
  beatMs: Ms,
): Ms {
  if (saved && saved.clientSessionId === row.clientSessionId) {
    return Math.max(saved.elapsedMs, row.elapsedMs);
  }
  if (row.elapsedMs === 0) return 0;
  return row.elapsedMs + Math.min(Math.max(0, now - row.lastBeatAt), beatMs);
}

/**
 * 이미 쌓인 기록이 덮는 마지막 시각. 시선 1초 기록 하나는 `[t_ms, t_ms + duration_ms]` 를 덮고,
 * 슬라이드 전환은 그 시각 한 점입니다. 기록이 없으면 0 입니다.
 */
export function recordedUntilMs({
  gaze,
  slideAtMs,
}: {
  gaze: { t_ms: Ms; duration_ms: Ms }[];
  slideAtMs: Ms[];
}): Ms {
  let until = 0;
  for (const s of gaze) until = Math.max(until, s.t_ms + s.duration_ms);
  for (const t of slideAtMs) until = Math.max(until, t);
  return until;
}

/** 장 넘김 하나. IndexedDB `slideChanges` 행과 같은 모양입니다 */
export interface SlideChange {
  atMs: Ms;
  slideNumber: number;
}

/**
 * 새로고침 직전까지 이 페이지에서 보낸 장 넘김 전부 (`useSttStream` `slideLog`). `pagehide` 에 적습니다.
 *
 * IndexedDB 기록만으로는 모자랍니다. 쓰기(`appendSlideChange`)가 비동기라 새로고침 직전에 넘긴 장은
 * 아직 없을 수 있고, 쓰기가 실패했을 수도 있습니다. 끊겨 있었다면 소켓에 쌓였던 그 장도 페이지와 함께
 * 사라집니다. 그러면 BE 에도 IndexedDB 에도 없어 장 구간 나누기에서 그 장의 말이 앞 장으로 들어갑니다.
 * 이건 동기라 새로고침이면 남습니다. 이어받을 때 IndexedDB 기록과 합칩니다 (`mergeSlideChanges`).
 */
export interface SavedSlides {
  clientSessionId: string;
  slides: SlideChange[];
}

const slidesKey = (takeId: string) => `rehearsal:slides:${takeId}`;

export function saveSlides(takeId: string, saved: SavedSlides): void {
  try {
    if (saved.slides.length === 0) sessionStorage.removeItem(slidesKey(takeId));
    else sessionStorage.setItem(slidesKey(takeId), JSON.stringify(saved));
  } catch {
    // 사생활 보호 모드 · 저장 공간 부족. IndexedDB 기록으로 이어받습니다
  }
}

/** 적어 둔 것을 꺼내고 지웁니다. 다른 세션이 적은 것 · 모양이 틀린 행은 버립니다 */
export function takeSlides(takeId: string, clientSessionId: string): SlideChange[] {
  try {
    const raw = sessionStorage.getItem(slidesKey(takeId));
    sessionStorage.removeItem(slidesKey(takeId));
    if (!raw) return [];
    const value = JSON.parse(raw) as Partial<SavedSlides>;
    if (value.clientSessionId !== clientSessionId || !Array.isArray(value.slides)) return [];
    return value.slides.filter(
      (s): s is SlideChange => typeof s?.atMs === 'number' && typeof s?.slideNumber === 'number',
    );
  } catch {
    return [];
  }
}

export function clearSlides(takeId: string): void {
  try {
    sessionStorage.removeItem(slidesKey(takeId));
  } catch {
    // 지우지 못해도 다음 Take 는 takeId 가 달라 읽지 않습니다
  }
}

/**
 * IndexedDB 기록과 새로고침 직전에 적어 둔 기록을 합칩니다. 같은 시각은 하나로 둡니다(앞의 것 —
 * BE 도 `(take_id, started_ms)` 로 같은 시각은 먼저 받은 것을 둡니다). 시각 순서로 돌려줍니다.
 */
export function mergeSlideChanges(...sources: readonly (readonly SlideChange[])[]): SlideChange[] {
  const byTime = new Map<Ms, SlideChange>();
  for (const source of sources) {
    for (const s of source) if (!byTime.has(s.atMs)) byTime.set(s.atMs, s);
  }
  return [...byTime.values()].sort((a, b) => a.atMs - b.atMs);
}

/** 마지막으로 보던 슬라이드. 전환 기록이 없으면 null — 처음부터 시작합니다 */
export function lastSlide(
  changes: { atMs: Ms; slideNumber: number }[],
): { slideNumber: number; atMs: Ms } | null {
  let last: { slideNumber: number; atMs: Ms } | null = null;
  for (const c of changes) {
    if (!last || c.atMs >= last.atMs) last = { slideNumber: c.slideNumber, atMs: c.atMs };
  }
  return last;
}

/**
 * 무대를 열 때 보낼 WS `slide` (`docs/ai/architecture.md` 1-4).
 *
 * 이어받았으면 장 넘김 기록을 **전부** 다시 보냅니다 (IndexedDB 기록에 새로고침 직전에 적어 둔 기록을
 * 합친 것, `mergeSlideChanges`). 끊긴 동안 소켓에 쌓였던 장 넘김은
 * 새로고침과 함께 사라졌고, 사라지는 페이지가 보낸 것도 닿았는지 모릅니다. 하나만 빠져도 장 구간
 * 나누기에서 그 장의 말이 앞 장으로 들어갑니다. 이미 간 것은 BE 가 `(take_id, started_ms)` 로
 * 거릅니다 (`coach-agent.md` 12-1). 한 Take 에 수십 개라 다시 보내도 부담이 없습니다.
 *
 * 기록이 없으면(처음 시작 · 기록을 못 읽음) 지금 장을 Take 시작(0)으로 보냅니다.
 */
export function openingSlides(
  history: readonly { atMs: Ms; slideNumber: number }[],
  current: number,
): SlideMessage[] {
  if (history.length === 0) return [{ type: 'slide', slide_number: current, t_ms: 0 }];
  return [...history]
    .sort((a, b) => a.atMs - b.atMs)
    .map((h) => ({ type: 'slide', slide_number: h.slideNumber, t_ms: h.atMs }));
}
