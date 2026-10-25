import { http, HttpResponse } from 'msw';
import type { TakeHistoryRow } from '@/types/takeHistory';
import { homeFixture } from './home';

/**
 * Take 목록 목 — `GET /api/takes/` (BE #398, `types/takeHistory.ts`).
 *
 * 화면 상태를 주소만 바꿔 볼 수 있게 피치 id 마다 모양을 고정해 둡니다.
 *   홈 목의 피치  Take 3개 — 번호 · 시간 · 대본 모드는 홈 목 그대로, 시선 엔진이 다른 Take(1) 포함.
 *                홈의 "Take 비교"가 여기로 옵니다
 *   p2          분석 중 · 분석 실패만 — 비교할 완료 Take 가 없고, 둘 다 발표 시간을 모름(null)
 *   그 밖의 id  BE 처럼 404 (없거나 남의 피치). UUID 꼴이 아니면 BE 처럼 422 입니다.
 *               `pitch_id` 없이 부르는 전체 목록은 그대로입니다
 */

const homePitch = homeFixture.pitches[0]!;
/** Take 가 없어도 있는 피치면 200 입니다 */
const pitchIds = new Set([...homeFixture.pitches.map((p) => p.pitch_id), 'p2']);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const base: TakeHistoryRow = {
  take_id: '',
  pitch_id: homePitch.pitch_id,
  pitch_title: homePitch.pitch_title,
  take_number: 0,
  started_at: '',
  status: 'COMPLETED',
  script_mode: 'HIGHLIGHT',
  duration_ms: 0,
  goal_ms: homePitch.pitch_time * 1000,
  audience_ratio: 0.74,
  script_ratio: 0.22,
  cpm: 352,
  filler_count: 7,
  missions_achieved: 0,
  missions_total: 1,
  gaze_engine: 'gaze_v1.1.0',
};

/** 홈 목의 Take 한 줄을 그대로 옮깁니다. FULL 은 HIGHLIGHT 로 합쳐졌습니다 (CLAUDE.md 7번) */
function fromHome(take_version: number, patch: Partial<TakeHistoryRow>): TakeHistoryRow {
  const t = homePitch.takes.find((x) => x.take_version === take_version)!;
  return {
    ...base,
    take_id: t.take_id,
    take_number: t.take_version,
    duration_ms: t.take_elapsed === null ? null : t.take_elapsed * 1000,
    goal_ms: t.take_time * 1000,
    script_mode: t.script_mode === 'KEYWORD' ? 'KEYWORD' : 'HIGHLIGHT',
    ...patch,
  };
}

// prettier-ignore
const rows: TakeHistoryRow[] = [
  { ...base, take_id: 'x2', pitch_id: 'p2', pitch_title: '투자 설명회', take_number: 2,
    started_at: '2026-10-07T21:05:00+09:00', status: 'ANALYZING', duration_ms: null },
  fromHome(3, { started_at: '2026-10-06T19:20:00+09:00' }),
  fromHome(2, { started_at: '2026-10-05T20:40:00+09:00', audience_ratio: 0.66, script_ratio: 0.3,
    cpm: 331, filler_count: 9, missions_achieved: 1, missions_total: 2 }),
  fromHome(1, { started_at: '2026-10-04T22:10:00+09:00', audience_ratio: 0.51, script_ratio: 0.44,
    cpm: 286, filler_count: 15, missions_total: 0, gaze_engine: 'gaze_v1.0.0' }),
  { ...base, take_id: 'x1', pitch_id: 'p2', pitch_title: '투자 설명회', take_number: 1,
    started_at: '2026-10-02T17:00:00+09:00', status: 'FAILED', duration_ms: null,
    audience_ratio: null, script_ratio: null, cpm: null, filler_count: null, gaze_engine: null },
];

export const takeHistoryHandlers = [
  http.get('*/api/takes/', ({ request }) => {
    const pitchId = new URL(request.url).searchParams.get('pitch_id');
    if (pitchId && !pitchIds.has(pitchId))
      return UUID.test(pitchId)
        ? HttpResponse.json({ code: 'PITCH_NOT_FOUND' }, { status: 404 })
        : HttpResponse.json({ code: 'VALIDATION_ERROR' }, { status: 422 });
    return HttpResponse.json({ takes: rows.filter((r) => !pitchId || r.pitch_id === pitchId) });
  }),
];
