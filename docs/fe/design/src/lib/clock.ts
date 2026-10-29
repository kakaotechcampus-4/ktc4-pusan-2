/** 09:42 / +00:24 형태로. 제품 `shared/lib/clock.ts`의 formatDuration과 같은 표기다. */
export function formatDuration(ms: number, showSign = false): string {
  const neg = ms < 0;
  const total = Math.floor(Math.abs(ms) / 1000);
  const mm = String(Math.floor(total / 60)).padStart(2, '0');
  const ss = String(total % 60).padStart(2, '0');
  if (!showSign) return `${mm}:${ss}`;
  return `${neg ? '+' : '-'}${mm}:${ss}`;
}
