import type { ScriptMode } from '@/types/api';
import type { TakeHistoryRow } from '@/types/takeHistory';

export const SCRIPT_MODE_LABEL: Record<ScriptMode, string> = {
  HIGHLIGHT: '전체 대본 + 하이라이트',
  KEYWORD: '핵심 키워드',
  OFF: '대본 없이',
};

/**
 * 서버가 문자열로 준 대본 보기 값을 한글로 (리포트의 지난 Take 비교, `ReviewResult.comparison`).
 * 표에 없는 값도 영어 코드로 보이지 않게 합니다. 모르는 값을 다른 방식 이름으로 바꿔 부르지 않습니다 —
 * 사실과 다른 이름을 보여 주게 됩니다
 */
export function scriptModeLabel(raw: string): string {
  // 예전 방식입니다. 그 방식으로 연습한 옛 Take 와 비교할 때 옵니다 (`normalizeScriptMode` 참고)
  if (raw === 'FULL') return '전체 대본';
  if (raw in SCRIPT_MODE_LABEL) return SCRIPT_MODE_LABEL[raw as ScriptMode];
  return '다른 방식';
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

/** 최신순 목록에서 엔진을 아는 가장 최근 Take를 비교 기준으로 씁니다. */
export function latestGazeEngine(rows: TakeHistoryRow[]) {
  return rows.find((r) => r.gaze_engine !== null)?.gaze_engine ?? null;
}

/** 기준 엔진과 다르거나 엔진을 모르는 Take의 시선 값은 나란히 비교하지 않습니다. */
export function gazeOffEngine(row: TakeHistoryRow, latestEngine: string | null) {
  return row.gaze_engine === null || row.gaze_engine !== latestEngine;
}
