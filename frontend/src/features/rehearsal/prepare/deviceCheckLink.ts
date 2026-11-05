import type { PracticeCombo, RehearsalTicket } from '@/types/api';

/** 장치 점검으로 가는 링크. `ButtonLink` 에 그대로 펼쳐 넘깁니다 */
export interface DeviceCheckLink {
  to: string;
  state: { practice: PracticeCombo } | undefined;
}

/**
 * 리허설 · 재시도 화면에서 장치 점검으로 돌아가는 링크.
 *
 * 장치 점검은 이번 연습의 조합을 라우터 state(`practice`)로만 받습니다. 피치 수정에서 예전 버전을
 * 골라 시작한 Take 가 실패해 돌아갈 때 조합을 넘기지 않으면, 장치 점검은 서버 최신 조합으로 엽니다.
 * 그래서 시작할 때 `ticket.practice` 에 적어 둔 조합을 다시 넘깁니다.
 *
 * 조합 없이(홈에서 바로) 시작한 Take 는 넘기지 않습니다 — 넘기면 장치 점검의 "새 대본을 나누는 중"
 * 안내가 사라집니다(조합이 없을 때만 뜹니다). 이 칸이 생기기 전에 저장된 세션도 없으니 지금처럼 갑니다.
 */
export function deviceCheckLink(
  ticket: Pick<RehearsalTicket, 'pitchId' | 'practice'>,
): DeviceCheckLink {
  return {
    to: `/pitch/${ticket.pitchId}/device-check`,
    state: ticket.practice ? { practice: ticket.practice } : undefined,
  };
}
