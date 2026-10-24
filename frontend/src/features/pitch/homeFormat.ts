export function duration(seconds: number | null) {
  if (seconds === null) return '—';
  return `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;
}

/**
 * 누적 발표 시간. 길이를 아는 Take 만 더하고, 하나도 모르면 null(—) 입니다.
 * 0 으로 채우면 "발표를 안 했다"로 읽힙니다 (CLAUDE.md 6번). 끝내지 않은 Take 는 길이가 없어 빠집니다.
 */
export function totalElapsed(takes: { take_elapsed: number | null }[]): number | null {
  const known = takes.flatMap((t) => (t.take_elapsed === null ? [] : [t.take_elapsed]));
  return known.length ? known.reduce((a, b) => a + b, 0) : null;
}
