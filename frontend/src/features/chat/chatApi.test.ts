import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

// 시험 환경에는 앱 주소가 없어 상대 경로를 부를 수 없습니다 — `apiBase` 가 읽기 전에 정합니다
vi.hoisted(() => {
  import.meta.env.VITE_API_BASE = 'http://localhost';
});
vi.mock('@/mocks/auth', () => ({ currentMockUserId: () => 'u' }));

import { createChatHandlers, type MockChatControls } from '@/mocks/chat';
import { ApiFailure } from '@/shared/api/tokenStore';
import type { RunView, SendMessageAccepted } from '@/types/chat';
import { chatApi, fetchMessages } from './chatApi';

/**
 * 실제 `chatApi` 를 목 BE(MSW) 위에서 그대로 부릅니다 — 통신 계층 시험은 같은 모양의 가짜 API 를 써서,
 * `chatApi.ts` 의 메서드 · 경로 · 쿼리 · 본문과 `apiRequest` 가 `Retry-After` 를 싣는 부분은 여기서만 봅니다(#953 리뷰).
 * 목이 BE 와 같은지는 `mocks/chat.contract.test.ts` 가 봅니다
 */

let controls: MockChatControls;
const server = setupServer();
/** 목이 받은 요청 — 메서드 · 경로(쿼리 포함) · 본문 */
let seen: { method: string; path: string; body: unknown }[];

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' });
  server.events.on('request:start', ({ request }) => {
    const url = new URL(request.url);
    const record = {
      method: request.method,
      path: url.pathname + url.search,
      body: null as unknown,
    };
    seen.push(record);
    request
      .clone()
      .text()
      .then((text) => {
        record.body = text ? JSON.parse(text) : null;
      })
      .catch(() => undefined);
  });
});
afterAll(() => server.close());
beforeEach(() => {
  seen = [];
  const chat = createChatHandlers(() => 'u');
  controls = chat.controls;
  server.resetHandlers(...chat.handlers);
});

let key = 0;
const message = (text: string) => ({
  conversation_id: null,
  scope_pitch_id: null,
  text,
  page: { route: '/', pitch_id: null, take_id: null, screen: null },
  client_request_id: `00000000-0000-4000-8000-${String(++key).padStart(12, '0')}`,
  again_of_message_id: null,
  choice: null,
});

const last = () => seen.at(-1)!;

describe('chatApi — 실제 호출 모양', () => {
  it('보내기는 POST /api/chat/messages 에 본문 그대로, 202 를 받는다', async () => {
    const request = message('지난번보다 청중을 더 봤어?');
    const accepted = await chatApi.send(request);
    await vi.waitFor(() =>
      expect(last()).toMatchObject({ method: 'POST', path: '/api/chat/messages', body: request }),
    );
    expect(accepted).toMatchObject({ status: 'QUEUED' });
  });

  it('실행 조회 · 대화의 실행 조회는 GET 에 after_seq 쿼리를 싣는다', async () => {
    const accepted = (await chatApi.send(message('질문'))) as SendMessageAccepted;
    const view = (await chatApi.run(accepted.run_id, 3)) as RunView;
    expect(last()).toMatchObject({
      method: 'GET',
      path: `/api/chat/runs/${accepted.run_id}?after_seq=3`,
    });
    expect(view.run_id).toBe(accepted.run_id);
    await chatApi.conversationRun(accepted.conversation_id, 7);
    expect(last()).toMatchObject({
      method: 'GET',
      path: `/api/chat/conversations/${accepted.conversation_id}/run?after_seq=7`,
    });
  });

  it('취소는 본문 없는 POST, 범위 바꾸기는 PATCH { pitch_id }', async () => {
    const accepted = (await chatApi.send(message('질문'))) as SendMessageAccepted;
    await chatApi.cancel(accepted.run_id);
    expect(last()).toMatchObject({
      method: 'POST',
      path: `/api/chat/runs/${accepted.run_id}/cancel`,
      body: null,
    });
    await chatApi.changeScope(accepted.conversation_id, null);
    await vi.waitFor(() =>
      expect(last()).toMatchObject({
        method: 'PATCH',
        path: `/api/chat/conversations/${accepted.conversation_id}/scope`,
        body: { pitch_id: null },
      }),
    );
  });

  it('되돌리기는 본문 없는 POST — 없는 행동은 404 ApiFailure', async () => {
    const accepted = (await chatApi.send(message('질문'))) as SendMessageAccepted;
    await expect(chatApi.undo(accepted.run_id, 'a-없음')).rejects.toMatchObject({
      status: 404,
      code: 'NOT_FOUND',
    });
    expect(last()).toMatchObject({
      method: 'POST',
      path: `/api/chat/runs/${accepted.run_id}/actions/${encodeURIComponent('a-없음')}/undo`,
      body: null,
    });
  });

  it('메시지 목록은 limit 쿼리로 읽고, 앞 페이지는 before_seq 를 더한다', async () => {
    const accepted = (await chatApi.send(message('질문'))) as SendMessageAccepted;
    await fetchMessages(accepted.conversation_id);
    expect(last().path).toBe(
      `/api/chat/conversations/${accepted.conversation_id}/messages?limit=30`,
    );
    await fetchMessages(accepted.conversation_id, 5);
    expect(last().path).toBe(
      `/api/chat/conversations/${accepted.conversation_id}/messages?limit=30&before_seq=5`,
    );
  });

  it('거절은 ApiFailure 로 — 429 는 Retry-After 초를 싣는다', async () => {
    controls.fail('AGENT_DAILY_LIMIT');
    const error = await chatApi.send(message('질문')).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiFailure);
    expect(error).toMatchObject({ status: 429, code: 'AGENT_DAILY_LIMIT' });
    expect((error as ApiFailure).retryAfterSec).toEqual(expect.any(Number));
  });
});
