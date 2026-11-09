import type { PlanStep, RunStatus } from '@/types/chat';
import { ANSWER_FAILED_TEXT } from './chatResponse';

/**
 * 안내 문구 (`docs/ai/features/chat-agent.md` 12-4 F8 표 · 7-9 · 7-10) — 화면이 고르는 규칙만 여기 둡니다.
 * 문서에 없는 문구는 "FE 가 정한 문구" 라고 적었습니다.
 */

/** 보내기가 거절된 코드 → 문구 (L3-17). 모르는 코드는 "지금은 답을 만들지 못했어요." */
export function sendFailedText(code: string): string {
  switch (code) {
    // 메시지 보내기 응답의 429 는 서비스 전체 하루 상한 (12-4 F8)
    case 'AGENT_BUSY':
      return '오늘은 사용량이 많아 잠시 뒤 다시 이용해 주세요.';
    case 'AGENT_DAILY_LIMIT':
      return '오늘 이용 횟수를 모두 썼으니 내일 다시 이용해 주세요.';
    case 'AGENT_CONTEXT_TOO_LARGE':
      return '자료가 너무 많으니 범위를 좁혀 다시 물어봐 주세요.';
    case 'VALIDATION_ERROR':
      return '메시지를 확인해 주세요.';
    case 'AGENT_REQUEST_CONFLICT':
      return '보낸 질문과 내용이 달라 받지 못했어요. 다시 보내 주세요.';
    case 'NOT_FOUND':
      return '대화를 찾을 수 없어요.';
    default:
      return ANSWER_FAILED_TEXT;
  }
}

/** 다른 탭의 실행이 있어 받지 못함(409 `AGENT_RUN_IN_PROGRESS`) — 상태 조회로 그 실행을 이어 봅니다 */
export const RUN_IN_PROGRESS_TEXT = '이 대화의 답변을 만들고 있어요.';

export interface EndNotice {
  text: string;
  /** [다시 보내기]를 둘지 — 같은 질문을 새 메시지로 */
  resend: boolean;
}

/**
 * 실행이 끝났을 때 답 아래에 붙일 안내 (12-4 F8). 붙일 것이 없으면 null.
 * - 끝까지 답했거나(`DONE`) 중간까지 답했으면(`PARTIAL`, "여기까지 확인했어요" 는 메시지에서) 붙이지 않습니다.
 * - 승인 대기에서 곧바로 끝났으면 BE 가 저장한 고정 문장만 보이고 '실행 만료 · 취소' 를 함께 보이지 않습니다.
 * - 범위 밖 거절(`REFUSED`)은 BE · AI 의 고정 문장이 답이라 붙이지 않습니다.
 * - 꺼짐(`AGENT_DISABLED`)은 문구 없이 버튼을 숨깁니다 — 부른 쪽이 처리합니다.
 */
export function runEndNotice(end: {
  status: RunStatus;
  errorCode: string | null;
  endedWhileWaiting: boolean;
}): EndNotice | null {
  const { status, errorCode } = end;
  if (status === 'DONE' || status === 'PARTIAL' || status === 'REFUSED') return null;
  if (errorCode === 'AGENT_DISABLED') return null;
  if (status === 'EXPIRED' || status === 'CANCELLED') {
    if (end.endedWhileWaiting) return null;
    return status === 'EXPIRED'
      ? { text: '시간이 지나 다시 물어봐 주세요.', resend: false }
      : { text: '요청을 취소했어요.', resend: false };
  }
  switch (errorCode) {
    case 'AGENT_EXPIRED':
      return { text: '시간이 지나 다시 물어봐 주세요.', resend: false };
    case 'AGENT_CANCELLED':
      return { text: '요청을 취소했어요.', resend: false };
    case 'AGENT_TIMEOUT':
      return { text: '답이 늦어지고 있으니 다시 보내 주세요.', resend: true };
    // 실행 오류의 AGENT_BUSY 는 동시 실행 — `retry_after_s` 까지 보내기를 막은 뒤 다시 보낼 수 있습니다
    case 'AGENT_BUSY':
      return { text: '요청이 많으니 잠시 뒤 다시 이용해 주세요.', resend: true };
    case 'AGENT_DAILY_LIMIT':
      return { text: '오늘 이용 횟수를 모두 썼으니 내일 다시 이용해 주세요.', resend: false };
    case 'AGENT_CONTEXT_TOO_LARGE':
      return { text: '자료가 너무 많으니 범위를 좁혀 다시 물어봐 주세요.', resend: false };
    case 'AGENT_STATE_INVALID':
      return {
        text: '실행 상태가 만료되었거나 업데이트되었으니 다시 물어봐 주세요.',
        resend: false,
      };
    case 'AGENT_OUTPUT_TOO_LARGE':
      return { text: '답이 너무 길어 완료하지 못했으니 질문 범위를 좁혀 주세요.', resend: true };
    default:
      return { text: ANSWER_FAILED_TEXT, resend: true };
  }
}

/** 승인 대기를 끝낸 BE 고정 문장 (7-10) — 이 문장으로 끝난 PARTIAL 은 "여기까지 확인했어요" 를 붙이지 않습니다 */
const CLOSED_WAITING_TEXTS = [
  '확인 시간이 지나 실행하지 않았어요. 필요하면 다시 말해 주세요.',
  '새 메시지를 받아 앞의 확인 요청은 취소했어요.',
  '확인 요청을 취소했어요.',
];

/**
 * 중간까지만 답한 메시지의 머리 — "여기까지 확인했어요. {남은 일}" (12-4 F8 `PARTIAL`). 붙이지 않으면 null.
 * 남은 일은 끝나지 않은 할 일(할 일 · 하는 중)입니다. 승인 대기가 만료 · 취소돼 BE 가 PARTIAL 로 저장한 메시지는
 * 고정 문장이 이미 사정을 알리므로 붙이지 않습니다(BE `close_waiting`)
 */
export function partialHeader(
  status: RunStatus | null,
  lastClaimText: string | null,
  plan: readonly PlanStep[],
): string | null {
  if (status !== 'PARTIAL') return null;
  if (lastClaimText !== null && CLOSED_WAITING_TEXTS.includes(lastClaimText)) return null;
  const left = plan.filter((p) => p.status === 'TODO' || p.status === 'DOING').map((p) => p.step);
  return left.length > 0
    ? `여기까지 확인했어요. 남은 일: ${left.join(' · ')}`
    : '여기까지 확인했어요.';
}

/** [이어서 확인]이 보내는 말 — FE 가 정한 문구입니다(문서: "같은 대화에 새 질문으로 보냄") */
export const CONTINUE_TEXT = '이어서 확인해 줘';

/** 승인 · 거절이 받아들여지지 않은 코드 → 문구 (L3-39 · L3-40) */
export function decisionFailedText(code: string): string {
  switch (code) {
    case 'APPROVAL_HASH_MISMATCH':
      return '요청이 변경되어 다시 확인이 필요해요.';
    case 'AGENT_EXPIRED':
      return '시간이 지나 다시 물어봐 주세요.';
    case 'AGENT_CANCELLED':
      return '요청을 취소했어요.';
    default:
      return ANSWER_FAILED_TEXT;
  }
}

/** 진행 중 상태 안내 — 조회 실패 · 늦어짐 · 취소 확인 (L3-19 · L3-21 · L3-34) */
export type RunNotice =
  | { kind: 'pollTrouble'; tooLarge: boolean }
  | { kind: 'slow' }
  | { kind: 'cancelChecking' }
  | { kind: 'cancelFailed' };

export function runNoticeText(notice: RunNotice): string {
  switch (notice.kind) {
    case 'pollTrouble':
      return notice.tooLarge
        ? '일부 진행 정보를 표시하지 못했어요.'
        : '진행 상태를 확인하지 못해 다시 연결하고 있어요.';
    case 'slow':
      return '결과 확인이 늦어지고 있으니 상태를 다시 확인해 주세요.';
    case 'cancelChecking':
      return '취소 여부를 확인하고 있어요.';
    case 'cancelFailed':
      // FE 가 정한 문구 — 문서에 없습니다
      return '취소하지 못했으니 다시 시도해 주세요.';
  }
}
