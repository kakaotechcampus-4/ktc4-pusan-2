import type { Tone } from '@/shared/ui/Badge';
import type { TakeEndReason, TakeStatus } from '@/types/api';

export interface TakeStatusView {
  label: string;
  tone: Tone;
  /** 이 Take 로 갈 곳. 볼 화면이 없으면(시작 전 · 연습 중 · 중단 · 실패) null */
  link: { to: string; text: string } | null;
}

/**
 * Take 상태 배지와 이동 경로 — 홈 · Take 기록이 같은 기준을 씁니다 (BE #490 표시안).
 * 점수 유무가 아니라 `status` 로 판단합니다. FAILED 중 사용자가 끝내지 못한 것(ABANDONED · CLOSED)은 "중단",
 * 나머지는 "실패" 입니다. `end_reason` 이 없는 이전 응답은 null 로 넘겨 "실패" 로 읽힙니다.
 */
export function takeStatusView(
  takeId: string,
  status: TakeStatus,
  endReason: TakeEndReason | null,
): TakeStatusView {
  const id = encodeURIComponent(takeId);
  switch (status) {
    case 'READY':
      return { label: '시작 전', tone: 'muted', link: null };
    case 'RUNNING':
      return { label: '연습 중', tone: 'good', link: null };
    case 'ANALYZING':
      return {
        label: '분석 중',
        tone: 'good',
        link: { to: `/takes/${id}/processing`, text: '진행 보기' },
      };
    case 'COMPLETED':
      return { label: '완료', tone: 'plain', link: { to: `/takes/${id}`, text: '리포트' } };
    case 'FAILED':
      return {
        label: endReason === 'ABANDONED' || endReason === 'CLOSED' ? '중단' : '실패',
        tone: 'muted',
        link: null,
      };
  }
}
