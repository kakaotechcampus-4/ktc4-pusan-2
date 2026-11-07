/**
 * 대화형 코치 에이전트 API 경로 — **잠정**.
 *
 * 경로 · 이름은 BE 가 정합니다(`docs/ai/features/chat-agent.md` 12-3 · 12-5). 아직 화면용 API 가 없어서
 * (#903 은 BE ↔ AI 흐름만) 동작 이름을 따라 FE 가 임시로 붙였습니다. 목(`mocks/chat.ts`)과 통신 계층이
 * **이 파일만** 보게 해서, BE 초안이 나오면 여기와 `types/chat.ts` 만 고칩니다.
 */
const BASE = '/api/chat';

export const chatPaths = {
  /** GET → `{enabled}` */
  availability: `${BASE}/availability`,
  /** GET `?pitch_id=` → 대화 목록 · POST → 새 대화 */
  conversations: `${BASE}/conversations`,
  /** GET → 메시지 목록 */
  messages: (conversationId: string) => `${BASE}/conversations/${conversationId}/messages`,
  /** PUT `{pitch_id}` → 범위 바꾸기 */
  scope: (conversationId: string) => `${BASE}/conversations/${conversationId}/scope`,
  /** POST → 202 메시지 접수 */
  send: `${BASE}/messages`,
  /** GET → 이 대화에서 끝나지 않은 실행 (12-3 "실행 상태 조회 | `run_id` 또는 `conversation_id`") */
  conversationRun: (conversationId: string) => `${BASE}/conversations/${conversationId}/run`,
  /** GET `?after_seq=` → 실행 상태 · 이벤트 */
  run: (runId: string) => `${BASE}/runs/${runId}`,
  /** POST → 실행 취소 */
  cancel: (runId: string) => `${BASE}/runs/${runId}/cancel`,
  /** POST `{decision, args_hash}` → 승인 · 거절 */
  decision: (actionId: string) => `${BASE}/actions/${actionId}/decision`,
  /** POST → 목표 저장 되돌리기 */
  undo: (actionId: string) => `${BASE}/actions/${actionId}/undo`,
  /** GET → 기억 설정 · 항목 · PUT `{enabled}` → 켜기 · 끄기 */
  memory: `${BASE}/memory`,
  /** DELETE → 전체 지우기 */
  memoryItems: `${BASE}/memory/items`,
  /** DELETE → 항목 하나 지우기 */
  memoryItem: (itemId: string) => `${BASE}/memory/items/${itemId}`,
} as const;
