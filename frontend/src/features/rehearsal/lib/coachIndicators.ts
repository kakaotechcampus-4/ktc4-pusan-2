/**
 * AI 코치의 상태 표시(`indicators`)를 화면 글자로 바꿉니다.
 *
 * 값은 판정 모듈의 `state` 입니다 (`docs/ai/architecture.md` 6-3). 판정은 AI 가 하고, 여기서는
 * 이름만 붙입니다 — 기준값을 FE 가 따로 두지 않습니다 (`architecture.md` 0절 "판정 기준은 기능이 갖는다").
 */
import type { IndicatorArea } from './sttProtocol';

/** 화면에 늘 같은 순서로 둡니다. 순서가 바뀌면 사용자가 찾느라 시선이 움직입니다 */
export const INDICATOR_AREAS: readonly IndicatorArea[] = [
  'GAZE',
  'SPEED',
  'VOLUME',
  'PAUSE',
  'FILLER',
  'TIME',
];

const AREA_LABEL: Record<IndicatorArea, string> = {
  GAZE: '시선',
  SPEED: '속도',
  VOLUME: '크기',
  PAUSE: '침묵',
  FILLER: '군더더기',
  TIME: '시간',
};

/**
 *   good     지금 좋음
 *   warn     고칠 것이 있음
 *   unknown  잴 수 없음 · 모르는 값
 */
export type IndicatorTone = 'good' | 'warn' | 'unknown';

const STATE: Record<string, { label: string; tone: IndicatorTone }> = {
  // gaze
  AUDIENCE: { label: '청중', tone: 'good' },
  SCRIPT: { label: '대본', tone: 'warn' },
  SCREEN: { label: '화면', tone: 'warn' },
  AWAY: { label: '딴 곳', tone: 'warn' },
  UNMEASURABLE: { label: '—', tone: 'unknown' },
  // pace · volume · pause · filler 공통
  NORMAL: { label: '좋음', tone: 'good' },
  UNKNOWN: { label: '—', tone: 'unknown' },
  SLOW: { label: '느림', tone: 'warn' },
  FAST: { label: '빠름', tone: 'warn' },
  LOW: { label: '작음', tone: 'warn' },
  SILENT: { label: '멈춤', tone: 'warn' },
  HIGH: { label: '많음', tone: 'warn' },
  // timing
  ON_TRACK: { label: '맞음', tone: 'good' },
  AHEAD: { label: '빠름', tone: 'warn' },
  BEHIND: { label: '늦음', tone: 'warn' },
  OVER: { label: '초과', tone: 'warn' },
};

export interface IndicatorView {
  area: IndicatorArea;
  label: string;
  value: string;
  tone: IndicatorTone;
}

/**
 * 받은 상태를 화면 순서대로 바꿉니다. 아직 받지 못한 영역은 빼고,
 * 모르는 값(AI 가 나중에 늘린 상태)은 '—' 로 둡니다 — 뜻을 모르는 값을 좋다 나쁘다 하지 않습니다.
 */
export function indicatorViews(
  indicators: Partial<Record<IndicatorArea, string>>,
): IndicatorView[] {
  return INDICATOR_AREAS.flatMap((area) => {
    const state = indicators[area];
    if (state === undefined) return [];
    const known = STATE[state] ?? { label: '—', tone: 'unknown' as const };
    return [{ area, label: AREA_LABEL[area], value: known.label, tone: known.tone }];
  });
}
