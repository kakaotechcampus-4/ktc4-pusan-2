import { apiRequest, postJson } from '@/shared/api/client';
import { chatPaths } from '@/shared/api/chatPaths';
import type {
  CreateConversationRequest,
  CreateMemoryOffConversationRequest,
  CreateConversationResponse,
  ScopeChangeResponse,
  SendMessageAccepted,
  SendMessageRequest,
} from '@/types/chat';

/**
 * 대화 에이전트 API — 통신 계층이 부르는 서버 호출만 모았습니다. 경로는 `chatPaths`(BE #933)이고,
 * 실패는 `apiRequest` 가 `ApiFailure`(상태 · 코드 · `Retry-After`)로 던집니다.
 *
 * 통신 계층이 이 모양에만 기대게 해서, 시험에서는 가짜로 바꿔 끼울 수 있습니다.
 */
export interface ChatApi {
  send: (request: SendMessageRequest) => Promise<SendMessageAccepted>;
  /**
   * 응답은 `unknown` — BE 가 아직 싣지 않는 칸(`events` · `cards` · `next_after_seq`)이 있어
   * 통신 계층이 읽으며 채웁니다. `after_seq` 는 (BE 미구현)이라 지금 BE 는 무시합니다
   */
  run: (runId: string, afterSeq: number) => Promise<unknown>;
  /** 이 대화의 마지막 실행(끝났어도). 한 번도 없으면 null */
  conversationRun: (conversationId: string) => Promise<unknown>;
  cancel: (runId: string) => Promise<unknown>;
  createConversation: (
    request: CreateConversationRequest | CreateMemoryOffConversationRequest,
  ) => Promise<CreateConversationResponse>;
  changeScope: (conversationId: string, pitchId: string | null) => Promise<ScopeChangeResponse>;
}

export const chatApi: ChatApi = {
  send: (request) => postJson<SendMessageAccepted>(chatPaths.send, request),
  run: (runId, afterSeq) =>
    apiRequest<unknown>(`${chatPaths.run(runId)}?after_seq=${encodeURIComponent(afterSeq)}`),
  conversationRun: (conversationId) =>
    apiRequest<unknown>(chatPaths.conversationRun(conversationId)),
  // BE 취소는 본문을 받지 않습니다. 빈 본문 대신 아무것도 보내지 않습니다
  cancel: (runId) => apiRequest<unknown>(chatPaths.cancel(runId), { method: 'POST' }),
  createConversation: (request) =>
    postJson<CreateConversationResponse>(chatPaths.conversations, request),
  changeScope: (conversationId, pitchId) =>
    apiRequest<ScopeChangeResponse>(chatPaths.scope(conversationId), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pitch_id: pitchId }),
    }),
};
