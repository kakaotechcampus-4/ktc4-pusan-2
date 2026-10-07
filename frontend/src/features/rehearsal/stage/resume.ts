import type { Ms } from '@/types/api';

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
 * 이건 동기라 새로고침이면 남고, 탭이 바뀌면 사라집니다(다른 탭은 하트비트로 이어받습니다).
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
 * 무대 시계를 어디서부터 다시 돌릴까.
 *
 * 1. 새로고침이면 `pagehide` 에 적어 둔 값이 있습니다. 정확합니다.
 * 2. 없으면(탭이 죽었다 · 다른 탭에서 열었다) 마지막 하트비트에서 추정합니다 —
 *    `elapsedMs + min(지금 − lastBeatAt, beatMs)`. 탭이 죽은 건 마지막 하트비트 뒤
 *    한 주기 안이라 대개 이전 탭이 보낸 시각보다 뒤입니다. 가려진 탭은 브라우저가 타이머를
 *    늦춰서 앞설 수도 있는데, 그래도 전사는 서버가 offset 이 뒤로 간 것을 보고 새 구간으로 받습니다.
 *    상한을 두는 이유는 다음 날 같은 주소로 들어와도 시간이 하루만큼 튀지 않게 하려는 것입니다.
 *    `elapsedMs` 가 0 이면 0 부터입니다 — 준비 화면에서 막 넘어온 행도 그렇고(`lastBeatAt` 이
 *    Take 를 만든 시각이라 그대로 더하면 처음부터 몇 초가 비어 시작합니다), 첫 하트비트 전에
 *    탭이 죽었으면 앞 5초가 겹칠 뿐입니다.
 *
 * 적어 둔 값과 하트비트 중 큰 쪽을 씁니다. 적어 둔 값이 더 작을 일은 없지만, 작으면 기록이 겹칩니다.
 */
export function resumeFromMs(
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

/** 코치가 마지막으로 말한 시각. 쿨다운을 이어가는 데 씁니다 */
export interface CoachHistory {
  lastFiredAt: Ms;
  lastByType: Record<string, Ms>;
}

/** 띄운 기록만 봅니다. 참은 기록은 쿨다운을 걸지 않습니다 */
export function coachHistory(rows: { atMs: Ms; type: string; fired: boolean }[]): CoachHistory {
  const history: CoachHistory = { lastFiredAt: -Infinity, lastByType: {} };
  for (const r of rows) {
    if (!r.fired) continue;
    history.lastFiredAt = Math.max(history.lastFiredAt, r.atMs);
    history.lastByType[r.type] = Math.max(history.lastByType[r.type] ?? -Infinity, r.atMs);
  }
  return history;
}
