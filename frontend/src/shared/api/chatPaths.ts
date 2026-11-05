/**
 * 대화형 코치 에이전트 API 경로 — BE #933 `feat/be-chat-api-834` (`module/chat/controller.py`) 에 맞췄습니다.
 * 되돌리기는 #935. 승인 · 거절 · 기억은 BE 가 아직 열지 않아 문서(12-3)대로 둔 **(BE 미구현)** 경로입니다.
 * 목(`mocks/chat.ts`)과 통신 계층이 이 파일만 봅니다.
 */
const BASE = '/api/chat';

export const chatPaths = {
  /** GET → `{enabled}` */
  availability: `${BASE}/availability`,
  /** GET `?pitch_id=` → 대화 목록 · POST → 새 대화 */
  conversations: `${BASE}/conversations`,
  /** GET `?before_seq=&limit=` → 메시지 목록 (최근부터 한 페이지) */
  messages: (conversationId: string) => `${BASE}/conversations/${conversationId}/messages`,
  /** PATCH `{pitch_id}` → 범위 바꾸기 (바뀐 대화) */
  scope: (conversationId: string) => `${BASE}/conversations/${conversationId}/scope`,
  /** POST → 202 메시지 접수 */
  send: `${BASE}/messages`,
  /** GET → 이 대화의 마지막 실행(끝났어도), 한 번도 없으면 null (12-3 "실행 상태 조회 | `run_id` 또는 `conversation_id`") */
  conversationRun: (conversationId: string) => `${BASE}/conversations/${conversationId}/run`,
  /** GET `?after_seq=` → 실행 상태 · 이벤트 */
  run: (runId: string) => `${BASE}/runs/${runId}`,
  /** POST → 실행 취소 */
  cancel: (runId: string) => `${BASE}/runs/${runId}/cancel`,
  /** (BE 미구현) POST `{decision, args_hash}` → 승인 · 거절 */
  decision: (actionId: string) => `${BASE}/actions/${actionId}/decision`,
  /** POST → 목표 저장 되돌리기 (BE #935) */
  undo: (runId: string, actionId: string) => `${BASE}/runs/${runId}/actions/${actionId}/undo`,
  /** (BE 미구현) GET → 기억 설정 · 항목 · PUT `{enabled}` → 켜기 · 끄기 */
  memory: `${BASE}/memory`,
  /** DELETE → 전체 지우기 */
  memoryItems: `${BASE}/memory/items`,
  /** DELETE → 항목 하나 지우기 */
  memoryItem: (itemId: string) => `${BASE}/memory/items/${itemId}`,
} as const;
