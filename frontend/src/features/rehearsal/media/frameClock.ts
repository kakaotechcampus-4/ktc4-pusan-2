/**
 * 분석 프레임 속도. AI v1 의 `analysis_fps` 와 같은 값입니다.
 *
 * 왜 제한하나 — 백프레셔만 있으면 워커가 처리하는 만큼 계속 보냅니다.
 * 모델이 한 장에 30ms 걸리면 초당 30장 가까이 돌고, 그 CPU 를 녹음·STT·화면이 나눠 씁니다.
 * AI 는 초당 8장을 기준으로 지연 예산(p95 125ms)을 잡았고, 1초 다수결은
 * 표본 4장이면 판정합니다(MIN_SAMPLES). 8장이면 충분합니다.
 */
export const ANALYSIS_FPS = 8;

/**
 * 다음 프레임을 보낼 시각. **평균 속도를 지키는 쪽으로** 계산합니다.
 *
 * `now + 간격` 으로 두면 rAF 가 16ms 단위로 늦게 도는 만큼 매번 밀려서
 * 8fps 가 7.5fps 가 됩니다. 그래서 직전 예정 시각에 간격을 더합니다.
 *
 * 다만 한 간격 넘게 뒤처졌으면(탭이 가려졌다 돌아옴, 처리가 길었음) 지금 기준으로
 * 다시 잡습니다 — 밀린 만큼 몰아서 보내면 워커에 한꺼번에 쌓입니다.
 */
export function nextFrameDue(prevDue: number, now: number, intervalMs: number): number {
  const next = prevDue + intervalMs;
  return next <= now - intervalMs ? now + intervalMs : next;
}
