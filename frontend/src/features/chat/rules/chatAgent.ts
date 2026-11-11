import type { RunStatus } from '@/types/chat';

/** 실행 종결 상태 (12-3 상태 전이). 종결 뒤에는 바뀌지 않습니다 — 조회도 진행 상태로 되돌리지 않습니다 */
export const TERMINAL_RUN_STATUSES: readonly RunStatus[] = [
  'DONE',
  'PARTIAL',
  'REFUSED',
  'FAILED',
  'CANCELLED',
  'EXPIRED',
];

export const isTerminalRun = (status: RunStatus) => TERMINAL_RUN_STATUSES.includes(status);
