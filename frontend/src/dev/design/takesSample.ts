/**
 * Take 목록 시안 데이터 — 비교(P7) · Best Take(P8) · Take 기록(P16)이 같이 씁니다. **시안 전용입니다.**
 *
 * 지표 이름은 AI 지표(`docs/ai/architecture.md` 6-3)에 맞췄습니다. 이 목록을 BE가 어떤 API로 줄지는 아직 없습니다.
 * 측정 못 한 값은 0이 아니라 null입니다 (CLAUDE.md 6번).
 */

export type TakeStatus = 'ANALYZING' | 'COMPLETED' | 'FAILED';
export type ScriptMode = 'HIGHLIGHT' | 'KEYWORD' | 'OFF';

export interface TakeRow {
  take_id: string;
  pitch_title: string;
  take_number: number;
  date: string;
  status: TakeStatus;
  script_mode: ScriptMode;
  duration_ms: number;
  goal_ms: number;
  audience_ratio: number | null;
  script_ratio: number | null;
  cpm: number | null;
  filler_count: number | null;
  /** 이 Take에 실린 미션 중 달성한 수 / 전체 */
  missions_achieved: number;
  missions_total: number;
  /** 시선 엔진 버전. 다르면 시선 값을 나란히 비교하지 않습니다 */
  gaze_engine: string;
  is_best: boolean;
}

export const SCRIPT_MODE_LABEL: Record<ScriptMode, string> = {
  HIGHLIGHT: '전체 대본',
  KEYWORD: '핵심 키워드',
  OFF: '대본 없이',
};

export const takes: TakeRow[] = [
  {
    take_id: 't4',
    pitch_title: '캡스톤 중간발표',
    take_number: 4,
    date: '10.06',
    status: 'COMPLETED',
    script_mode: 'KEYWORD',
    duration_ms: 278_000,
    goal_ms: 300_000,
    audience_ratio: 0.74,
    script_ratio: 0.22,
    cpm: 352,
    filler_count: 7,
    missions_achieved: 0,
    missions_total: 1,
    gaze_engine: 'gaze_v1.1.0',
    is_best: false,
  },
  {
    take_id: 't3',
    pitch_title: '캡스톤 중간발표',
    take_number: 3,
    date: '10.05',
    status: 'COMPLETED',
    script_mode: 'HIGHLIGHT',
    duration_ms: 298_000,
    goal_ms: 300_000,
    audience_ratio: 0.66,
    script_ratio: 0.3,
    cpm: 331,
    filler_count: 9,
    missions_achieved: 1,
    missions_total: 2,
    gaze_engine: 'gaze_v1.1.0',
    is_best: true,
  },
  {
    take_id: 't2',
    pitch_title: '캡스톤 중간발표',
    take_number: 2,
    date: '10.04',
    status: 'COMPLETED',
    script_mode: 'HIGHLIGHT',
    duration_ms: 342_000,
    goal_ms: 300_000,
    audience_ratio: null,
    script_ratio: null,
    cpm: 298,
    filler_count: 12,
    missions_achieved: 0,
    missions_total: 2,
    gaze_engine: 'unavailable',
    is_best: false,
  },
  {
    take_id: 't1',
    pitch_title: '캡스톤 중간발표',
    take_number: 1,
    date: '10.03',
    status: 'COMPLETED',
    script_mode: 'HIGHLIGHT',
    duration_ms: 361_000,
    goal_ms: 300_000,
    audience_ratio: 0.51,
    script_ratio: 0.44,
    cpm: 286,
    filler_count: 15,
    missions_achieved: 0,
    missions_total: 0,
    gaze_engine: 'gaze_v1.0.0',
    is_best: false,
  },
];

/** P16 — 여러 피치에 걸친 기록 */
export const allTakes: TakeRow[] = [
  {
    ...takes[0]!,
    take_id: 'x1',
    pitch_title: '투자 설명회',
    take_number: 2,
    date: '10.07',
    status: 'ANALYZING',
    missions_total: 1,
  },
  ...takes,
  {
    ...takes[3]!,
    take_id: 'x2',
    pitch_title: '투자 설명회',
    take_number: 1,
    date: '10.02',
    status: 'FAILED',
    audience_ratio: null,
    script_ratio: null,
    cpm: null,
    filler_count: null,
  },
];

export function pct(v: number | null) {
  return v === null ? '—' : `${Math.round(v * 100)}%`;
}
