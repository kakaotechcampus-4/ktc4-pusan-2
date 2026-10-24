import type { ScriptMode, TakeStatus } from './api';

/**
 * Take 목록 — `GET /api/takes/` (`?pitch_id=` 로 한 피치만). **BE 에 아직 없는 API 입니다 (FE 제안).**
 * Take 기록(P16) · Take 비교(P7)가 같은 응답을 씁니다. 최신 Take 가 앞입니다.
 *
 * BE 그대로 snake_case 입니다. 지표 이름은 AI 지표(`docs/ai/architecture.md` 6-3)에 맞췄습니다.
 * 측정 못 한 값은 0이 아니라 null 입니다 (CLAUDE.md 6번).
 */
export interface TakeHistoryRow {
  take_id: string;
  pitch_id: string;
  pitch_title: string;
  take_number: number;
  /** ISO 8601. 연습을 시작한 시각 */
  started_at: string;
  status: TakeStatus;
  script_mode: ScriptMode;
  duration_ms: number;
  goal_ms: number;
  audience_ratio: number | null;
  script_ratio: number | null;
  cpm: number | null;
  filler_count: number | null;
  /** 이 Take 에 실린 미션 중 달성한 수 / 전체 */
  missions_achieved: number;
  missions_total: number;
  /** 시선 엔진 버전. 다르면 시선 값을 나란히 비교하지 않습니다. 시선을 재지 못했으면 null */
  gaze_engine: string | null;
}

export interface TakeHistoryResponse {
  takes: TakeHistoryRow[];
}
