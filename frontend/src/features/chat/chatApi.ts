import { apiRequest, postJson } from '@/shared/api/client';
import { chatPaths } from '@/shared/api/chatPaths';
import type {
  ConversationRunView,
  CreateConversationRequest,
  CreateConversationResponse,
  RunView,
  ScopeChangeResponse,
  SendMessageAccepted,
  SendMessageRequest,
} from '@/types/chat';

/**
 * 대화 에이전트 API — 통신 계층이 부르는 서버 호출만 모았습니다. 경로는 `chatPaths` 의 잠정 경로이고,
 * 실패는 `apiRequest` 가 `ApiFailure`(상태 · 코드 · `Retry-After`)로 던집니다.
 *
 * 통신 계층이 이 모양에만 기대게 해서, 시험에서는 가짜로 바꿔 끼울 수 있습니다.
 */
export interface ChatApi {
  send: (request: SendMessageRequest) => Promise<SendMessageAccepted>;
  run: (runId: string, afterSeq: number) => Promise<RunView>;
  conversationRun: (conversationId: string) => Promise<ConversationRunView>;
  cancel: (runId: string) => Promise<RunView>;
  createConversation: (request: CreateConversationRequest) => Promise<CreateConversationResponse>;
  changeScope: (conversationId: string, pitchId: string | null) => Promise<ScopeChangeResponse>;
}

export const chatApi: ChatApi = {
  send: (request) => postJson<SendMessageAccepted>(chatPaths.send, request),
  run: (runId, afterSeq) =>
    apiRequest<RunView>(`${chatPaths.run(runId)}?after_seq=${encodeURIComponent(afterSeq)}`),
  conversationRun: (conversationId) =>
    apiRequest<ConversationRunView>(chatPaths.conversationRun(conversationId)),
  cancel: (runId) => postJson<RunView>(chatPaths.cancel(runId), {}),
  createConversation: (request) =>
    postJson<CreateConversationResponse>(chatPaths.conversations, request),
  changeScope: (conversationId, pitchId) =>
    apiRequest<ScopeChangeResponse>(chatPaths.scope(conversationId), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pitch_id: pitchId }),
    }),
};
