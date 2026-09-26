/**
 * 기록 쓰기 실패 장부.
 *
 * ── 왜 필요한가 ────────────────────────────────────────────────────
 * 발표 중에 IndexedDB 쓰기가 실패하면 화면에는 아무 흔적도 남지 않습니다.
 * 그렇다고 자리마다 `console.error` 를 흩뿌리면, 저장소가 가득 찬 경우처럼
 * 초당 계속 실패하는 상황에서 콘솔이 같은 줄로 덮여 버립니다.
 *
 * 그래서 **종류별로 첫 건만 찍고 나머지는 셉니다.** 종료 시점에 이 장부를
 * 한 번 읽으면 "무엇을 몇 건 잃었나" 가 한 줄로 나옵니다.
 *
 * ── 왜 IndexedDB 가 아니라 메모리인가 ──────────────────────────────
 * 실패의 가장 흔한 원인이 저장소가 가득 찬 것입니다. 그 상황에서 실패 기록을
 * 같은 저장소에 쓰면 그것도 실패합니다. 장부는 탭이 살아 있는 동안만 유효하면
 * 충분합니다 — 탭이 죽으면 어차피 종료 처리 자체가 없습니다.
 */

/** 무엇을 쓰다 실패했나. 종료 로그에 이 이름 그대로 나옵니다 */
export type WriteKind =
  'gazeDecision' | 'slideChange' | 'coachLog' | 'gazeExcluded' | 'engineVersion' | 'gazePerf';

const ledger = new Map<string, Map<WriteKind, number>>();

/**
 * 실패 한 건을 적습니다. **그 종류의 첫 건일 때만** true 를 돌려줍니다 —
 * 호출부가 "처음 한 번만" 할 일(사용자 통보·제외 표시)을 여기에 맞춰 겁니다.
 */
export function noteWriteFailure(
  clientSessionId: string,
  kind: WriteKind,
  error: unknown,
): boolean {
  let perSession = ledger.get(clientSessionId);
  if (!perSession) {
    perSession = new Map();
    ledger.set(clientSessionId, perSession);
  }

  const next = (perSession.get(kind) ?? 0) + 1;
  perSession.set(kind, next);

  const first = next === 1;
  // 첫 건만 찍습니다. 같은 실패가 이어지면 개수로만 쌓입니다
  if (first) console.error(`[기록] ${kind} 저장 실패`, { clientSessionId, error });
  return first;
}

/** 종류별 실패 건수. 실패가 없으면 빈 객체입니다 */
export function readWriteFailures(clientSessionId: string): Partial<Record<WriteKind, number>> {
  const perSession = ledger.get(clientSessionId);
  if (!perSession) return {};
  return Object.fromEntries(perSession);
}

/** 한 건이라도 있었나 — 종료 화면이 경고를 띄울지 정하는 값입니다 */
export function hasWriteFailures(clientSessionId: string): boolean {
  const perSession = ledger.get(clientSessionId);
  return perSession !== undefined && perSession.size > 0;
}

/** 세션이 끝나면 비웁니다. 한 탭에서 Take 를 여러 번 하면 장부가 계속 쌓입니다 */
export function clearWriteFailures(clientSessionId: string): void {
  ledger.delete(clientSessionId);
}
