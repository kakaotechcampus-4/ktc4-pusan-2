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

/** 지표 이름은 `architecture.md` 6-3 */
const METRIC: Record<
  string,
  { label: string; unit: 'ratio' | 'count' | 'perMin' | 'cpm' | 'ms' | 'db' }
> = {
  audience_ratio: { label: '청중 응시', unit: 'ratio' },
  script_ratio: { label: '대본 응시', unit: 'ratio' },
  reading_ratio: { label: '대본 읽기', unit: 'ratio' },
  reading_runs: { label: '대본 읽기 횟수', unit: 'count' },
  cpm: { label: '말하기 속도', unit: 'cpm' },
  pause_count: { label: '멈춤', unit: 'count' },
  voice_diff_db: { label: '평소 대비 목소리', unit: 'db' },
  low_ratio: { label: '작은 목소리', unit: 'ratio' },
  long_silence_count: { label: '긴 침묵', unit: 'count' },
  filler_count: { label: '군더더기', unit: 'count' },
  filler_per_min: { label: '분당 군더더기', unit: 'perMin' },
  duration_ms: { label: '발표 시간', unit: 'ms' },
  slide_duration_ms: { label: '장 시간', unit: 'ms' },
  content_coverage: { label: '핵심 내용 전달', unit: 'ratio' },
  critical_fact_accuracy: { label: '핵심 수치 정확도', unit: 'ratio' },
};

/** 모르는 지표는 이름을 그대로 보여 줍니다 — AI 가 지표를 늘려도 화면이 깨지지 않게 */
export function metricLabel(metric: string) {
  return METRIC[metric]?.label ?? metric;
}

export function fmt(metric: string, v: number) {
  switch (METRIC[metric]?.unit) {
    case 'ratio':
      return `${Math.round(v * 100)}%`;
    case 'count':
      return `${v}회`;
    case 'perMin':
      return `${v}회/분`;
    case 'cpm':
      return `${v}자/분`;
    case 'ms':
      return formatDuration(v);
    case 'db':
      return `${v}dB`;
    default:
      return String(v);
  }
}

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
