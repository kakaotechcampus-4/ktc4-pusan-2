import { http, HttpResponse } from 'msw';
import type { TakeHistoryRow } from '@/types/takeHistory';

/**
 * Take 목록 목 — `GET /api/takes/` (BE 에 아직 없는 API, `types/takeHistory.ts`).
 *
 * 화면 상태를 주소만 바꿔 볼 수 있게 피치 id 마다 모양을 고정해 둡니다.
 *   p1        Take 4개 — 시선 측정 제외(t2) · 시선 엔진이 다른 Take(t1) 포함
 *   p2        분석 중 · 분석 실패만 — 비교할 완료 Take 가 없음
 *   그 밖의 id Take 없음
 */

const base: TakeHistoryRow = {
  take_id: 't4',
  pitch_id: 'p1',
  pitch_title: '캡스톤 중간발표',
  take_number: 4,
  started_at: '2026-10-06T19:20:00+09:00',
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
};

// prettier-ignore
const rows: TakeHistoryRow[] = [
  { ...base, take_id: 'x2', pitch_id: 'p2', pitch_title: '투자 설명회', take_number: 2,
    started_at: '2026-10-07T21:05:00+09:00', status: 'ANALYZING' },
  base,
  { ...base, take_id: 't3', take_number: 3, started_at: '2026-10-05T20:40:00+09:00',
    script_mode: 'HIGHLIGHT', duration_ms: 298_000, audience_ratio: 0.66, script_ratio: 0.3,
    cpm: 331, filler_count: 9, missions_achieved: 1, missions_total: 2 },
  { ...base, take_id: 't2', take_number: 2, started_at: '2026-10-04T22:10:00+09:00',
    script_mode: 'HIGHLIGHT', duration_ms: 342_000, audience_ratio: null, script_ratio: null,
    cpm: 298, filler_count: 12, missions_total: 2, gaze_engine: null },
  { ...base, take_id: 't1', take_number: 1, started_at: '2026-10-03T18:30:00+09:00',
    script_mode: 'HIGHLIGHT', duration_ms: 361_000, audience_ratio: 0.51, script_ratio: 0.44,
    cpm: 286, filler_count: 15, missions_total: 0, gaze_engine: 'gaze_v1.0.0' },
  { ...base, take_id: 'x1', pitch_id: 'p2', pitch_title: '투자 설명회', take_number: 1,
    started_at: '2026-10-02T17:00:00+09:00', status: 'FAILED', audience_ratio: null,
    script_ratio: null, cpm: null, filler_count: null, gaze_engine: null },
];

export const takeHistoryHandlers = [
  http.get('*/api/takes/', ({ request }) => {
    const pitchId = new URL(request.url).searchParams.get('pitch_id');
    return HttpResponse.json({ takes: rows.filter((r) => !pitchId || r.pitch_id === pitchId) });
  }),
];
