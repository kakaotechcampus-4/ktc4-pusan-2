// @vitest-environment jsdom
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('@/mocks/auth', () => ({ currentMockUserId: () => 'u' }));
import { createChatHandlers } from '@/mocks/chat';
import { chatPaths } from '@/shared/api/chatPaths';
import type { RunView, SendMessageAccepted } from '@/types/chat';
import { canSend, chatTicket, isCurrent, pendingSendTicket, useChatStore } from './chatStore';

/**
 * 저장소를 목 BE 와 붙여 통신 계층이 할 순서대로 돌립니다 — 저장소만 보는 시험이 놓친,
 * 실제 응답 순서에서 잠금이 남는 경우를 잡습니다
 */
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

const store = () => useChatStore.getState();
const base = 'http://localhost';
const draft = {
  text: '비교해 줘',
  page: { route: '/', pitch_id: null, take_id: null, screen: null },
  again_of_message_id: null,
  choice: null,
};

const post = (path: string, body?: unknown) =>
  fetch(base + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

const sendPending = async () =>
  (await (await post(chatPaths.send, store().pending!.request)).json()) as SendMessageAccepted;

/** 보내기: 보관한 본문을 그대로 보냅니다 */
async function send(text: string) {
  const ticket = store().beginSend({ ...draft, text })!;
  const accepted = await sendPending();
  expect(store().beginRun(ticket, accepted.run_id, accepted.conversation_id)).toBe(true);
  return accepted;
}

/** 조회 한 번 — 지금 것이면 단계 · 커서 · 종결을 반영 */
async function poll() {
  const ticket = chatTicket();
  const run = store().run!;
  const response = await fetch(`${base}${chatPaths.run(run.runId)}?after_seq=${run.afterSeq}`);
  const view = (await response.json()) as RunView;
  if (!isCurrent(ticket)) return view;
  const last = view.events.at(-1);
  if (last) store().advanceCursor(run.runId, last.event_seq);
  if (view.status === 'QUEUED' || view.status === 'RUNNING' || view.status === 'WAITING_APPROVAL')
    store().setRunPhase(run.runId, view.status);
  else store().endRun(run.runId, view.retry_after_s);
  return view;
}

/** 끝나거나 승인 대기가 될 때까지 조회 */
async function untilSettled() {
  for (let i = 0; i < 20 && store().run; i++) {
    const view = await poll();
    if (view.status === 'WAITING_APPROVAL') return view;
  }
  return null;
}

beforeEach(() => {
  sessionStorage.clear();
  server.resetHandlers(...createChatHandlers(() => 'u').handlers);
  store().signOut();
  store().setUser('u');
  store().openChat({ pitchId: null, title: null });
});

describe('목 BE 와 붙인 순서', () => {
  it('승인 대기 중 새 메시지와 [취소]가 겹쳐도 끝나면 다시 보낼 수 있다', async () => {
    const first = await send('목표로 잡아 줘');
    expect((await untilSettled())?.status).toBe('WAITING_APPROVAL');

    const ticket = store().beginSend({ ...draft, text: '아니 그냥 비교해 줘' })!;
    expect(store().beginCancel(first.run_id)).toBe(true);
    const accepted = await sendPending();
    store().beginRun(ticket, accepted.run_id, accepted.conversation_id);
    // 앞 실행의 취소 응답은 지켜보던 실행이 바뀐 뒤에 옵니다
    const cancelled = (await (await post(chatPaths.cancel(first.run_id))).json()) as RunView;
    store().endRun(first.run_id);
    expect(cancelled.status).toBe('CANCELLED');

    await untilSettled();
    expect([store().run, store().cancelling, canSend(store())]).toEqual([null, null, true]);
  });

  it('새 대화의 접수 응답을 잃고 새로고침해도, 보관한 본문을 같은 키로 다시 보내 이어 간다', async () => {
    store().beginSend(draft);
    const lost = await sendPending();
    // 202 를 받기 전에 새로고침
    useChatStore.setState({ userId: null });
    store().setUser('u');
    expect([store().conversationId, canSend(store())]).toEqual([null, false]);

    const again = await sendPending();
    expect(again).toEqual(lost);
    expect(store().beginRun(pendingSendTicket()!, again.run_id, again.conversation_id)).toBe(true);
    await untilSettled();
    expect([store().conversationId, canSend(store())]).toEqual([lost.conversation_id, true]);
  });
});

describe('대화의 실행 조회 · 409 · 기억 없이 (네 번째 리뷰)', () => {
  const convRun = async (conversationId: string) =>
    (await (await fetch(base + chatPaths.conversationRun(conversationId))).json()) as {
      run: RunView | null;
    };

  /** 다른 탭: 같은 대화에 직접 보냄 */
  const otherTabSends = (conversationId: string) =>
    post(chatPaths.send, {
      ...draft,
      conversation_id: conversationId,
      scope_pitch_id: null,
      client_request_id: `other-${Math.random()}`,
    });

  it('409(다른 탭이 실행 중)면 대화의 실행을 찾아 이어 가고, 그동안 다시 보내지 못한다', async () => {
    const first = await send('비교해 줘');
    await untilSettled();
    const conversationId = first.conversation_id;
    await otherTabSends(conversationId);

    const ticket = store().beginSend(draft)!;
    const response = await post(chatPaths.send, store().pending!.request);
    expect(response.status).toBe(409);
    store().conflictSend(ticket);
    // 조회가 끝나기 전에는 잠금 — 리뷰가 찾은 "다시 보낸 말이 사라짐" 이 생기지 않습니다
    expect(canSend(store())).toBe(false);

    const lookupTicket = chatTicket();
    const found = (await convRun(conversationId)).run;
    expect(found).not.toBeNull();
    store().resolveConversation(lookupTicket, {
      runId: found!.run_id,
      phase: found!.status as 'RUNNING',
    });
    await untilSettled();
    expect([store().run, canSend(store())]).toEqual([null, true]);
    expect((await send('이번엔 되나?')).run_id).toBeTruthy();
  });

  it("'기억 없이' 면 대화를 먼저 만들고 보낸다 — 그 대화에는 기억이 저장되지 않는다", async () => {
    store().setMemoryOff(true);
    const create = store().beginCreate()!;
    const created = (await (
      await post(chatPaths.conversations, {
        scope_pitch_id: null,
        page: draft.page,
        memory_off: true,
      })
    ).json()) as { conversation_id: string };
    store().finishCreate(create, created.conversation_id);

    const accepted = await send('짧게 설명해 줘, 기억해');
    expect(accepted.conversation_id).toBe(created.conversation_id);
    await untilSettled();
    const page = (await (
      await fetch(base + chatPaths.messages(created.conversation_id))
    ).json()) as { items: { cards: { type: string }[] }[] };
    expect(page.items.at(-1)!.cards).toEqual([]);
    expect(store().memoryOff).toBe(true);
  });
});
