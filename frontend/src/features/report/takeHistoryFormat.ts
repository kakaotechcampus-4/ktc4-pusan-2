import type { ScriptMode } from '@/types/api';
import type { TakeHistoryRow } from '@/types/takeHistory';

export const SCRIPT_MODE_LABEL: Record<ScriptMode, string> = {
  HIGHLIGHT: '전체 대본 + 하이라이트',
  KEYWORD: '핵심 키워드',
  OFF: '대본 없이',
};

export function pct(v: number | null) {
  return v === null ? '—' : `${Math.round(v * 100)}%`;
}

/** `10.06` — 이 브라우저 시간대의 날짜 */
export function takeDay(iso: string) {
  const d = new Date(iso);
  return `${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`;
}

/** 비교는 분석이 끝난 Take 만 씁니다 */
export function completedTakes(rows: TakeHistoryRow[]) {
  return rows.filter((r) => r.status === 'COMPLETED');
}

/**
 * 시선 값을 나란히 놓으면 안 되는 Take 인가. 같은 사람도 시선 엔진이 다르면 숫자가 달라져서,
 * 최신 Take 의 엔진과 다른 Take 는 시선 값을 비교하지 않습니다. 시선을 못 잰 Take(null)도 여기 듭니다.
 */
export function gazeOffEngine(row: TakeHistoryRow, latestEngine: string | null) {
  return row.gaze_engine === null || row.gaze_engine !== latestEngine;
}
