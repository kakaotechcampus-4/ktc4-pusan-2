import { fetchActiveTake, replaceTake } from '@/shared/api/take';
import { ApiFailure } from '@/shared/api/tokenStore';
import type { ActiveTake } from '@/types/take';

/** 발표 중인 Take 를 이만큼 기다려도 답이 없으면 묻지 못한 것으로 봅니다 */
export const ACTIVE_TAKE_TIMEOUT_MS = 3_000;

/**
 * 시작 전에 다른 창 · 기기에서 발표 중인 Take 가 있는지 묻습니다 (#719).
 *
 * **묻지 못하면 없는 것으로 보고 시작합니다.** BE 가 아직 이 API 를 열지 않았거나(404) 네트워크가
 * 흔들려도 지금처럼 시작은 되어야 합니다. 정말 겹쳤으면 연결이 `TOO_MANY_STREAMS` 로 알립니다.
 * BE 는 오디오가 멈춘 연결은 알리지 않아서(새 Take 가 정리함) 여기서 null 이어도 겹칠 수는 있습니다.
 *
 * 답이 늦어도 같습니다 — `apiRequest` 에는 시간 제한이 없어, 기다리기만 하면 시작 버튼이
 * "연습을 여는 중…" 에 멈춥니다. 늦게 온 답은 버립니다
 */
export async function activeTakeOrNone(
  fetch: () => Promise<ActiveTake | null> = fetchActiveTake,
  timeoutMs = ACTIVE_TAKE_TIMEOUT_MS,
): Promise<ActiveTake | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error('TIMEOUT')), timeoutMs);
  });
  try {
    return await Promise.race([fetch(), timeout]);
  } catch (e) {
    console.warn('[장치 점검] 발표 중인 Take 를 묻지 못해 그대로 시작합니다', e);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 사용자가 끝내기로 고른 Take 를 끝냅니다. 끝나면 이어서 새 Take 를 만들어도 됩니다.
 *
 * 그사이 그 Take 가 스스로 끝났으면(`ANALYZING` · `FAILED`) BE 가 지금 상태를 돌려줘 그대로 성공입니다.
 * 404 는 이미 지워진 Take 라 끝낼 것이 없습니다. 그 밖의 실패는 던집니다 — 끝내지 못한 채 시작하면
 * 새 연결이 `TOO_MANY_STREAMS` 로 거절됩니다
 */
export async function endActiveTake(
  take: ActiveTake,
  replace: (take: ActiveTake) => Promise<unknown> = replaceTake,
): Promise<void> {
  try {
    await replace(take);
  } catch (e) {
    if (e instanceof ApiFailure && e.status === 404) return;
    throw e;
  }
}
