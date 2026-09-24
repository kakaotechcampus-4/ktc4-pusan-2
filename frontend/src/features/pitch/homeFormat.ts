export function duration(seconds: number | null) {
  if (seconds === null) return '—';
  return `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;
}
