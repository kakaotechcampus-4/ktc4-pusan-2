import { fmt, metricLabel } from '@/shared/lib/metricLabels';
import type { ProposedGoal } from '@/types/chat';

/** 목표 한 줄 — "청중 응시 · 3장 · Take 2 31%보다 높게" */
export function goalText(
  goal: Pick<ProposedGoal, 'metric' | 'slide_number' | 'operator' | 'base'>,
) {
  const where = goal.slide_number === null ? null : `${goal.slide_number}장`;
  // Take 번호를 모르면 값만 — "Take null 31%" 처럼 보이지 않게
  const take = goal.base.take_number === null ? '' : `Take ${goal.base.take_number} `;
  const base = `${take}${fmt(goal.metric, goal.base.value)}`;
  const range = goal.base.range;
  const rangeText =
    range && range.low !== null && range.high !== null
      ? `${fmt(goal.metric, range.low)} ~ ${fmt(goal.metric, range.high)} 안으로`
      : '정한 범위 안으로';
  const how = {
    gt_base: `${base}보다 높게`,
    lt_base: `${base}보다 낮게`,
    in_range: rangeText,
  }[goal.operator];
  return [metricLabel(goal.metric), where, how].filter(Boolean).join(' · ');
}

type GoalLike = Parameters<typeof goalText>[0];

/** 목표 모양이면 그 목표 — 승인 확인 카드의 전 · 후를 목표 문장으로 그릴 때 씁니다. 아니면 null */
export function asGoal(v: unknown): GoalLike | null {
  if (typeof v !== 'object' || v === null) return null;
  const g = v as Record<string, unknown>;
  const base = g.base as Record<string, unknown> | null | undefined;
  const ok =
    typeof g.metric === 'string' &&
    (g.operator === 'gt_base' || g.operator === 'lt_base' || g.operator === 'in_range') &&
    (g.slide_number === null || g.slide_number === undefined || Number.isInteger(g.slide_number)) &&
    typeof base === 'object' &&
    base !== null &&
    Number.isInteger(base.take_number) &&
    typeof base.value === 'number';
  return ok ? ({ slide_number: null, ...g } as GoalLike) : null;
}
