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
