import { ApiFailure } from '@/shared/api/tokenStore';

/**
 * 발표 전 점검 결과(`POST …/calibration`)를 **실시간 연결 전에** 몇 번 다시 보냅니다 (#1179).
 *
 * 서버는 Take 가 시작 전(READY)일 때만 받고, 같은 Take 로 다시 보내면 덮어씁니다(BE `save_calibration` upsert).
 * 그래서 같은 본문으로 다시 보내도 안전하고, 실시간 연결이 Take 를 시작하기 전에 끝내야 합니다 —
 * 장치 점검이 이 함수를 기다린 뒤에 무대로 넘어갑니다.
 *
 * **시도마다 제한 시간을 둡니다** (#1184 리뷰). 요청이 답 없이 걸리면 다시 보내기로도 넘어가지 못하고,
 * Take 는 이미 만들어졌는데 무대로 가지 못합니다. 제한 시간이 지나면 `AbortController` 로 요청을 실제로
 * 끊습니다 — `Promise.race` 로 기다리기만 그만두면 요청이 뒤에 남습니다. 끊긴 요청(`AbortError`)은 연결
 * 실패처럼 다시 보냅니다. 가장 오래 걸려도 `CALIBRATION_TIMEOUT_MS` × 3 + 대기 1.5초(약 13.5초)입니다.
 *
 * 다시 보내도 같은 답이 오는 실패(4xx — 이미 시작한 Take · 본문 거절)는 다시 보내지 않습니다. 시간 초과(408) ·
 * 너무 잦음(429) · 서버 오류(5xx) · 연결 실패 · 제한 시간 초과는 다시 보냅니다.
 */

/** 다시 보내기 전에 기다리는 시간. 길이가 곧 다시 보내는 횟수입니다 (처음 1번 + 2번) */
export const CALIBRATION_RETRY_DELAYS_MS = [500, 1000] as const;

/** 한 번 보낼 때의 제한 시간 */
export const CALIBRATION_TIMEOUT_MS = 4000;

/** 끝내 응답을 받지 못했을 때 무대가 띄우는 안내. 저장은 됐는데 응답만 잃었을 수 있어 단정하지 않습니다 */
export const BASE_VOICE_UNCONFIRMED_TEXT =
  '목소리 기준이 저장됐는지 확인하지 못했어요. 저장되지 않았으면 발표 처음 목소리로 잡아요.';

function retryable(err: unknown): boolean {
  if (!(err instanceof ApiFailure)) return true;
  return err.status >= 500 || err.status === 408 || err.status === 429;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * 응답을 받았으면 true. 끝내 못 받았으면 false — 원인은 콘솔에 남깁니다.
 * `send` 는 받은 `signal` 을 요청에 넘겨야 합니다 — 그래야 제한 시간에 실제로 끊깁니다.
 */
export async function sendCalibration(
  send: (signal: AbortSignal) => Promise<unknown>,
  {
    wait = sleep,
    timeoutMs = CALIBRATION_TIMEOUT_MS,
  }: { wait?: (ms: number) => Promise<void>; timeoutMs?: number } = {},
): Promise<boolean> {
  for (let attempt = 0; ; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      await send(controller.signal);
      return true;
    } catch (err: unknown) {
      const delay = CALIBRATION_RETRY_DELAYS_MS[attempt];
      if (delay === undefined || !retryable(err)) {
        console.error('[장치 점검] 보정 요약을 보내지 못했습니다', err);
        return false;
      }
      await wait(delay);
    } finally {
      clearTimeout(timer);
    }
  }
}
