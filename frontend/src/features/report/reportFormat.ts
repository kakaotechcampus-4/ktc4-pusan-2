import { formatDuration } from '@/shared/lib/clock';
import type { AnalysisStage, Area, Operator } from '@/types/report';

/* ── 분석 대기 단계 ─────────────────────────────────────────────── */

export type StepState = 'DONE' | 'RUNNING' | 'WAITING' | 'PENDING';

/** 화면의 세 단계 — BE 흐름 그대로입니다 (`docs/ai/architecture.md` 1-3) */
const STEP_STAGE: AnalysisStage[] = ['FINALIZING', 'SCORING', 'REVIEWING'];

/**
 * BE 단계 하나를 화면 세 단계의 상태로 펼칩니다. 단계가 아직 없으면(null) 첫 단계가 도는 중입니다.
 * 대본 확인(`AWAITING_CONFIRMATION`)은 채점 단계에 붙은 기다림입니다.
 */
export function stepStates(stage: AnalysisStage | null): StepState[] {
  if (stage === 'AWAITING_CONFIRMATION') return ['DONE', 'WAITING', 'PENDING'];
  const current = stage === 'DONE' ? STEP_STAGE.length : STEP_STAGE.indexOf(stage ?? 'FINALIZING');
  return STEP_STAGE.map((_, i) => {
    if (i < current) return 'DONE';
    if (i === current) return 'RUNNING';
    return 'PENDING';
  });
}

/* ── 이름 · 숫자 ────────────────────────────────────────────────── */

const AREA: Record<Area, string> = {
  GAZE: '시선',
  SPEED: '말 속도',
  VOLUME: '소리 크기',
  PAUSE: '침묵',
  FILLER: '군더더기',
  CONTENT: '내용 전달',
  TIME: '시간',
};

export const OP: Record<Operator, string> = { LT: '미만', LTE: '이하', GT: '초과', GTE: '이상' };

// 지표 이름 · 값 표기는 대화 에이전트와 함께 써서 shared 로 옮겼습니다
export { fmt, metricLabel } from '@/shared/lib/metricLabels';

/** 없음은 0% 가 아니라 — 로 (CLAUDE.md 6번) */
export function pct(v: number | null) {
  return v === null ? '—' : `${Math.round(v * 100)}%`;
}

/** 발표 시간을 모르면 00:00 이 아니라 — 로 (CLAUDE.md 6번) */
export function duration(ms: number | null) {
  return ms === null ? '—' : formatDuration(ms);
}

export function where(area: string, slide: number | null) {
  const label = Object.hasOwn(AREA, area) ? AREA[area as Area] : area;
  return slide === null ? `${label} · 발표 전체` : `${label} · ${slide}번 장`;
}

/** ISO 시각 → `2026. 10. 6. 오후 4:38` */
export function endedAt(iso: string | null) {
  if (iso === null) return null;
  return new Date(iso).toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' });
}
