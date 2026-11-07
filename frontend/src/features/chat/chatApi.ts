import { apiRequest, readJsonWithin, ResponseTooLargeError } from '@/shared/api/client';
import { chatPaths } from '@/shared/api/chatPaths';
import type {
  ActionDecisionRequest,
  CreateConversationRequest,
  CreateMemoryOffConversationRequest,
  CreateConversationResponse,
  MessagePage,
  ScopeChangeResponse,
  SendMessageAccepted,
  SendMessageRequest,
} from '@/types/chat';
import { MAX_RESPONSE_BYTES } from './chatResponse';

/** 실행 조회 · 메시지 목록은 2 MiB 까지만 읽습니다 — 넘으면 `ResponseTooLargeError` (L3-22) */
const within = readJsonWithin(MAX_RESPONSE_BYTES);

/**
 * 대화 에이전트 API — 통신 계층이 부르는 서버 호출만 모았습니다. 경로는 `chatPaths`(BE #933)이고,
 * 실패는 `apiRequest` 가 `ApiFailure`(상태 · 코드 · `Retry-After`)로 던집니다.
 *
 * 통신 계층이 이 모양에만 기대게 해서, 시험에서는 가짜로 바꿔 끼울 수 있습니다.
 */
export interface ChatApi {
  /** `signal` 은 [다시 확인]을 거듭 누를 때 앞 시도를 끊는 데 씁니다(다시 보내지는 않습니다) */
  send: (request: SendMessageRequest, signal?: AbortSignal) => Promise<SendMessageAccepted>;
  /**
   * 실행 조회 · 대화의 실행 · 취소 · 승인 · 거절의 응답은 `unknown` — 통신 계층이 모양을 검사하며 읽습니다.
   * `signal` 은 통신 계층이 시간 초과 · 대화 바꾸기 때 걸린 요청을 끊는 데 씁니다
   */
  run: (runId: string, afterSeq: number, signal?: AbortSignal) => Promise<unknown>;
  /** 이 대화의 마지막 실행(끝났어도). 한 번도 없으면 null. `afterSeq` 뒤의 이벤트도 싣습니다 */
  conversationRun: (
    conversationId: string,
    afterSeq: number,
    signal?: AbortSignal,
  ) => Promise<unknown>;
  cancel: (runId: string, signal?: AbortSignal) => Promise<unknown>;
  decide: (
    runId: string,
    actionId: string,
    request: ActionDecisionRequest,
    signal?: AbortSignal,
  ) => Promise<unknown>;
  createConversation: (
    request: CreateConversationRequest | CreateMemoryOffConversationRequest,
    signal?: AbortSignal,
  ) => Promise<CreateConversationResponse>;
  changeScope: (
    conversationId: string,
    pitchId: string | null,
    signal?: AbortSignal,
  ) => Promise<ScopeChangeResponse>;
  /** 대화 목록 — 범위 바꾸기의 결과를 모를 때 서버의 범위를 다시 읽습니다 */
  listConversations: (signal?: AbortSignal) => Promise<unknown>;
}

const postBody = (body: unknown, signal?: AbortSignal): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
  signal,
});

export const chatApi: ChatApi = {
  send: (request, signal) =>
    apiRequest<SendMessageAccepted>(chatPaths.send, postBody(request, signal)),
  run: (runId, afterSeq, signal) =>
    apiRequest<unknown>(
      `${chatPaths.run(runId)}?after_seq=${encodeURIComponent(afterSeq)}`,
      { signal },
      within,
    ),
  conversationRun: (conversationId, afterSeq, signal) =>
    apiRequest<unknown>(
      `${chatPaths.conversationRun(conversationId)}?after_seq=${encodeURIComponent(afterSeq)}`,
      { signal },
      within,
    ),
  // BE 취소는 본문을 받지 않습니다. 빈 본문 대신 아무것도 보내지 않습니다
  cancel: (runId, signal) =>
    apiRequest<unknown>(chatPaths.cancel(runId), { method: 'POST', signal }, within),
  decide: (runId, actionId, request, signal) =>
    apiRequest<unknown>(chatPaths.decision(runId, actionId), postBody(request, signal), within),
  createConversation: (request, signal) =>
    apiRequest<CreateConversationResponse>(chatPaths.conversations, postBody(request, signal)),
  changeScope: (conversationId, pitchId, signal) =>
    apiRequest<ScopeChangeResponse>(chatPaths.scope(conversationId), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pitch_id: pitchId }),
      signal,
    }),
  listConversations: (signal) => apiRequest<unknown>(chatPaths.conversations, { signal }, within),
};

/** 메시지 목록 한 번에 읽는 개수 — BE 기본 30, 상한 50 */
export const MESSAGES_PAGE = 30;
/** 한 페이지가 2 MiB 를 넘으면 이만큼씩 줄여 다시 읽습니다 — 무거운 답이 몇 개 쌓여도 대화를 열 수 있게 */
const SMALLER_PAGES = [10, 3, 1];

/**
 * 메시지 목록 한 페이지 — 최근부터, `beforeSeq` 가 있으면 그 앞. 화면(8단계)이 부르고,
 * 문장 · 카드는 `readMessage` 로 읽은 뒤 그립니다.
 * 응답이 2 MiB 를 넘으면 한 번에 읽는 개수를 줄여(30 → 10 → 3 → 1) 다시 읽습니다 — 받은 페이지의
 * `next_before_seq` 로 나머지를 이어 읽으면 됩니다. 메시지 하나도 넘으면 `ResponseTooLargeError`
 */
export async function fetchMessages(
  conversationId: string,
  beforeSeq: number | null = null,
): Promise<MessagePage> {
  let error: unknown = null;
  for (const limit of [MESSAGES_PAGE, ...SMALLER_PAGES]) {
    try {
      return await fetchMessagePage(conversationId, beforeSeq, limit);
    } catch (e) {
      if (!(e instanceof ResponseTooLargeError)) throw e;
      error = e;
    }
  }
  throw error;
}

async function fetchMessagePage(
  conversationId: string,
  beforeSeq: number | null,
  limit: number,
): Promise<MessagePage> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (beforeSeq !== null) params.set('before_seq', String(beforeSeq));
  const page = await apiRequest<MessagePage>(
    `${chatPaths.messages(conversationId)}?${params.toString()}`,
    {},
    within,
  );
  // 최신 아님 사유가 빠져 와도(예전 BE) 빈 목록으로
  return {
    ...page,
    messages: page.messages.map((m) => ({ ...m, stale_reasons: m.stale_reasons ?? [] })),
  };
}
