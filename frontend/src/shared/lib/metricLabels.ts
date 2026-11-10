import { formatDuration } from './clock';
import type { Dimension, DimensionStatus, JudgeStatus } from '@/types/report';

/**
 * 지표 · 영역 이름과 값 표기 — 리포트와 대화 에이전트(목표 · 참고 결과 카드)가 같은 이름을 씁니다.
 * feature 끼리는 서로 가져다 쓰지 않아(FRONTEND_CONVENTION.md 5) 여기 둡니다.
 */

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
  // 장치 점검에서 잰 기준 목소리(`base_level_db`)와의 차이입니다 — 크기 자체가 아니라 변화라서 이렇게 부릅니다 (#1179)
  voice_diff_db: { label: '기준 대비 변화', unit: 'db' },
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
  return Object.hasOwn(METRIC, metric) ? METRIC[metric]!.label : metric;
}

export function fmt(metric: string, v: number) {
  switch (Object.hasOwn(METRIC, metric) ? METRIC[metric]!.unit : undefined) {
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
      return signedDb(v);
    default:
      return String(v);
  }
}

/**
 * dB 는 기준과의 차이라 부호를 늘 붙입니다 — "+0.5dB" · "−1.1dB" · "0dB" (#1179).
 * 빼기는 하이픈이 아니라 빼기 기호(U+2212)로 씁니다. `-0` 도 "0dB" 입니다
 */
function signedDb(v: number) {
  if (v > 0) return `+${v}dB`;
  if (v < 0) return `−${-v}dB`;
  return '0dB';
}

/** 리뷰 영역 이름 */
export const DIMENSION_LABEL: Record<Dimension, string> = {
  audience_gaze: '청중 응시',
  script_independence: '대본에서 벗어나기',
  pacing_rhythm: '속도 · 리듬',
  vocal_delivery: '목소리',
  fluency: '군더더기 없이',
  content_delivery: '내용 전달',
};

/** 리뷰 영역 상태 */
export const DIMENSION_STATUS_LABEL: Record<DimensionStatus, string> = {
  PRIORITY: '먼저 고칠 것',
  IMPROVED: '나아짐',
  STRENGTH: '강점',
  STABLE: '유지',
  NOT_EVALUABLE: '판단 불가',
};

/** 모르는 영역 · 상태는 받은 글자 그대로 */
export const dimensionLabel = (d: string) =>
  Object.hasOwn(DIMENSION_LABEL, d) ? DIMENSION_LABEL[d as Dimension] : d;
export const dimensionStatusLabel = (s: string) =>
  Object.hasOwn(DIMENSION_STATUS_LABEL, s) ? DIMENSION_STATUS_LABEL[s as DimensionStatus] : s;

/** 미션 · 사용자 평가 기준 판정 */
export const JUDGE_STATUS_LABEL: Record<JudgeStatus, string> = {
  ACHIEVED: '달성',
  PARTIAL: '거의 달성',
  FAILED: '미달성',
  NOT_EVALUABLE: '판단 불가',
};

export const judgeStatusLabel = (s: string) =>
  Object.hasOwn(JUDGE_STATUS_LABEL, s) ? JUDGE_STATUS_LABEL[s as JudgeStatus] : s;
