// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';
import { announceSignedOut } from '@/shared/lib/sessionSignal';
import {
  WHOLE_SCOPE,
  bindChatToSession,
  canChangeScope,
  canSend,
  chatTicket,
  isCurrent,
  pendingSendTicket,
  useChatStore,
  type ChatScope,
} from './chatStore';

const store = () => useChatStore.getState();
const pitchA: ChatScope = { pitchId: 'pitch-a', title: '졸업 발표' };
const pitchB: ChatScope = { pitchId: 'pitch-b', title: '중간 발표' };

/** 보내기 → 접수까지 — 통신 계층이 할 순서 */
function sendAndAccept(runId: string, conversationId: string) {
  const ticket = store().beginSend();
  expect(ticket).not.toBeNull();
  expect(store().beginRun(ticket!, runId, conversationId)).toBe(true);
  return ticket!;
}

/** 대화를 고르고 그 대화의 실행 상태를 읽음 */
function openConversation(conversationId: string, scope: ChatScope = pitchA) {
  store().selectConversation(conversationId, scope);
  expect(store().resolveConversation(chatTicket(), null)).toBe(true);
}

/** 새로고침: 메모리는 사라지고 sessionStorage 만 남습니다 */
function reload(userId: string) {
  useChatStore.setState({ userId: null });
  store().setUser(userId);
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 30));

beforeEach(() => {
  sessionStorage.clear();
  store().signOut();
  useChatStore.setState({ coveredBy: 0, openAfterCover: false });
  store().setUser('user-1');
});

describe('열기 · 범위', () => {
  it('처음 열면 연 화면의 범위로, 대화를 시작한 뒤에는 화면을 옮겨도 범위가 그대로다', () => {
    store().openChat(pitchA);
    expect(store()).toMatchObject({ open: true, scope: pitchA, conversationId: null });
    sendAndAccept('run-1', 'conv-1');
    store().endRun('run-1');
    store().closeChat();
    store().openChat(WHOLE_SCOPE);
    expect(store()).toMatchObject({ open: true, scope: pitchA, conversationId: 'conv-1' });
  });

  it('대화를 시작하기 전에는 다시 열 때 그 화면의 범위로 맞춘다 — 칩으로 직접 고른 범위는 두고', () => {
    store().openChat(pitchA);
    store().closeChat();
    store().openChat(pitchB);
    expect(store().scope).toEqual(pitchB);
    expect(store().chooseScope(WHOLE_SCOPE)).toBe(true);
    store().openChat(pitchA);
    expect(store().scope).toEqual(WHOLE_SCOPE);
  });

  it('범위를 모르면 "확인 중"(null)으로 두고 보내지 않는다 — 알게 되면 맞춘 뒤 보낼 수 있다', () => {
    store().openChat(null);
    expect([store().scope, canSend(store()), store().beginSend()]).toEqual([null, false, null]);
    store().syncScreenScope(pitchA);
    expect(canSend(store())).toBe(true);
  });

  it('새 대화의 첫 보내기 중에는 화면을 옮겨도 범위가 바뀌지 않고, 보낸 범위로 대화가 정해진다', () => {
    store().openChat(pitchA);
    const ticket = store().beginSend()!;
    store().openChat(pitchB);
    store().syncScreenScope(null);
    expect(store().scope).toEqual(pitchA);
    store().beginRun(ticket, 'run-1', 'conv-1');
    store().endRun('run-1');
    expect(store()).toMatchObject({ scope: pitchA, scopeFixed: true });
    expect(canSend(store())).toBe(true);
  });

  it('새 대화의 첫 보내기가 접수되지 않으면, 범위는 다시 화면을 따라갈 수 있다', () => {
    store().openChat(pitchA);
    store().failSend(store().beginSend()!);
    store().openChat(pitchB);
    expect(store().scope).toEqual(pitchB);
  });

  it('새 대화의 범위 칩은 바로 바꾸고, 있는 대화는 서버 응답까지 잠근다 — 실패하면 앞 범위로', () => {
    store().openChat(pitchA);
    expect(store().beginScopeChange(pitchB)).toBeNull();
    expect(store().chooseScope(pitchB)).toBe(true);

    openConversation('conv-1', pitchA);
    expect(store().chooseScope(pitchB)).toBe(false);
    const change = store().beginScopeChange(pitchB)!;
    expect([store().scope, canSend(store()), canChangeScope(store())]).toEqual([
      pitchB,
      false,
      false,
    ]);
    store().finishScopeChange(change, null);
    expect([store().scope, canSend(store())]).toEqual([pitchA, true]);

    const ok = store().beginScopeChange(WHOLE_SCOPE)!;
    store().finishScopeChange(ok, WHOLE_SCOPE);
    store().finishScopeChange(ok, pitchB);
    expect(store().scope).toEqual(WHOLE_SCOPE);
  });

  it('범위 변경 중에 대화를 옮기면 늦은 응답은 받지 않는다', () => {
    openConversation('conv-1', pitchA);
    const change = store().beginScopeChange(pitchB)!;
    openConversation('conv-2', WHOLE_SCOPE);
    store().finishScopeChange(change, pitchB);
    expect(store().scope).toEqual(WHOLE_SCOPE);
  });

  it('서버가 확정한 범위는 지금 실행의 응답일 때만, 사용자가 바꾸는 중이 아닐 때만 반영한다', () => {
    openConversation('conv-1', WHOLE_SCOPE);
    sendAndAccept('run-1', 'conv-1');
    const poll = chatTicket();
    store().applyServerScope(poll, pitchB);
    expect(store().scope).toEqual(pitchB);

    store().endRun('run-1');
    store().applyServerScope(poll, pitchA);
    expect(store().scope).toEqual(pitchB);

    const change = store().beginScopeChange(WHOLE_SCOPE)!;
    store().applyServerScope(chatTicket(), pitchA);
    expect(store().scope).toEqual(WHOLE_SCOPE);
    store().finishScopeChange(change, WHOLE_SCOPE);
  });

  it("'기억 없이' 는 새 대화에서 보내기 전에만 고를 수 있고, 새로고침해도 남는다", () => {
    store().openChat(pitchA);
    expect(store().setMemoryOff(true)).toBe(true);
    reload('user-1');
    expect(store().memoryOff).toBe(true);
    const ticket = store().beginSend()!;
    expect(store().setMemoryOff(false)).toBe(false);
    store().beginRun(ticket, 'run-1', 'conv-1');
    expect(store().setMemoryOff(false)).toBe(false);
    expect(store().memoryOff).toBe(true);
  });
});

describe('보내기 · 접수', () => {
  beforeEach(() => store().openChat(pitchA));

  it('보내기를 시작하면 접수 전에도 바로 잠근다 — 두 번 보내기 · 범위 칩을 막는다', () => {
    const ticket = store().beginSend()!;
    expect([store().beginSend(), store().chooseScope(pitchB)]).toEqual([null, false]);
    store().failSend(ticket);
    expect(canSend(store())).toBe(true);
  });

  it('미확정이어도 잠금을 풀지 않고, 같은 sendId 로 다시 보내 접수를 받는다', () => {
    const ticket = store().beginSend()!;
    store().markUnconfirmed(ticket);
    expect([canSend(store()), store().pending?.unconfirmed]).toEqual([false, true]);
    expect(pendingSendTicket()?.sendId).toBe(ticket.sendId);
    expect(store().beginRun(pendingSendTicket()!, 'run-1', 'conv-1')).toBe(true);
    expect(store().pending).toBeNull();
  });

  it('앞 보내기의 늦은 접수는 다음 보내기를 가로채지 못한다', () => {
    const a = store().beginSend()!;
    store().failSend(a);
    const b = store().beginSend()!;
    expect(store().beginRun(a, 'run-a', 'conv-a')).toBe(false);
    expect(store().pending?.sendId).toBe(b.sendId);
    expect(store().beginRun(b, 'run-b', 'conv-b')).toBe(true);
    expect(store().run?.runId).toBe('run-b');
  });

  it('같은 실행의 접수가 다시 와도 실행 · 커서를 그대로 둔다', () => {
    const ticket = sendAndAccept('run-1', 'conv-1');
    store().setRunPhase('run-1', 'RUNNING');
    store().advanceCursor('run-1', 7);
    expect(store().beginRun(ticket, 'run-1', 'conv-1')).toBe(true);
    expect(store().run).toMatchObject({ runId: 'run-1', phase: 'RUNNING', afterSeq: 7 });
  });

  it('대기 · 실행 중에는 보내기와 칩을 막고, 승인 대기에서는 보내기만 허용한다', () => {
    sendAndAccept('run-1', 'conv-1');
    expect(canSend(store())).toBe(false);
    store().setRunPhase('run-1', 'RUNNING');
    expect([canSend(store()), canChangeScope(store())]).toEqual([false, false]);
    store().setRunPhase('run-1', 'WAITING_APPROVAL');
    expect([canSend(store()), canChangeScope(store())]).toEqual([true, false]);
    // 승인 대기 중 보낸 새 메시지는 BE 가 앞 확인을 취소하고 새 실행으로 받습니다
    sendAndAccept('run-2', 'conv-1');
    expect(store().run).toMatchObject({ runId: 'run-2', phase: 'QUEUED' });
  });

  it('취소를 누르면 확정될 때까지 새 보내기를 막는다 — 취소 요청이 실패하면 다시 취소할 수 있다', () => {
    sendAndAccept('run-1', 'conv-1');
    store().setRunPhase('run-1', 'WAITING_APPROVAL');
    expect(store().beginCancel('run-1')).toBe(true);
    expect([canSend(store()), store().beginCancel('run-1')]).toEqual([false, false]);
    store().abortCancel('run-1');
    expect(canSend(store())).toBe(true);
    store().beginCancel('run-1');
    store().endRun('run-1');
    expect([store().cancelling, canSend(store())]).toEqual([null, true]);
  });

  it('로그인 전 · 로그아웃 뒤에는 보내지 않고, 늦게 온 접수도 붙지 않는다', () => {
    const ticket = store().beginSend()!;
    store().signOut();
    store().openChat(pitchA);
    expect(store().beginSend()).toBeNull();
    expect(store().beginRun(ticket, 'run-late', 'conv-late')).toBe(false);
    expect(store().run).toBeNull();
  });

  it('보내는 사이 새 대화를 시작하면 늦게 온 접수는 붙지 않는다', () => {
    const ticket = store().beginSend()!;
    store().startNewConversation(pitchB);
    expect(store().beginRun(ticket, 'run-late', 'conv-late')).toBe(false);
    expect(store()).toMatchObject({
      run: null,
      conversationId: null,
      scope: pitchB,
      pending: null,
    });
  });
});

describe('대화 고르기 · 실행', () => {
  beforeEach(() => store().openChat(pitchA));

  it('대화를 고르면 실행 상태를 읽을 때까지 잠그고, 찾은 실행을 붙인다', () => {
    store().selectConversation('conv-1', pitchA);
    expect([canSend(store()), canChangeScope(store())]).toEqual([false, false]);
    expect(store().resolveConversation(chatTicket(), { runId: 'run-9', phase: 'RUNNING' })).toBe(
      true,
    );
    expect(store()).toMatchObject({
      syncing: false,
      run: { runId: 'run-9', conversationId: 'conv-1', phase: 'RUNNING', afterSeq: 0 },
    });
  });

  it('다른 대화로 옮긴 뒤 온 앞 대화의 상태는 받지 않는다', () => {
    store().selectConversation('conv-1', pitchA);
    const ticket = chatTicket();
    store().selectConversation('conv-2', pitchB);
    expect(store().resolveConversation(ticket, { runId: 'run-1', phase: 'RUNNING' })).toBe(false);
    expect([store().run, store().syncing]).toEqual([null, true]);
  });

  it('실행 중에도 다른 대화 · 새 대화로 옮길 수 있다 — 앞 실행은 놓고, 늦은 응답은 버린다', () => {
    sendAndAccept('run-1', 'conv-1');
    const poll = chatTicket();
    store().selectConversation('conv-2', pitchB);
    expect([store().run, isCurrent(poll)]).toEqual([null, false]);
    store().startNewConversation(pitchA);
    expect(canSend(store())).toBe(true);
  });

  it('커서는 그 실행에서 앞으로만 가고, 단계는 대기로 되돌아가지 않는다', () => {
    sendAndAccept('run-1', 'conv-1');
    expect(store().advanceCursor('run-1', 3)).toBe(true);
    expect(
      [3, 2]
        .map((seq) => store().advanceCursor('run-1', seq))
        .concat(store().advanceCursor('run-2', 9)),
    ).toEqual([false, false, false]);
    store().setRunPhase('run-1', 'RUNNING');
    store().setRunPhase('run-1', 'QUEUED');
    store().setRunPhase('run-2', 'WAITING_APPROVAL');
    expect(store().run).toMatchObject({ runId: 'run-1', phase: 'RUNNING', afterSeq: 3 });
  });

  it('조회 표는 실행이 끝나면 지금 것이 아니다 — 목록은 새 표로 다시 읽는다', () => {
    sendAndAccept('run-1', 'conv-1');
    const poll = chatTicket();
    store().endRun('run-1');
    expect([isCurrent(poll), isCurrent(chatTicket())]).toEqual([false, true]);
  });

  it('사용자가 바뀌면 세대가 올라 앞 세대의 응답을 버린다', () => {
    const before = chatTicket();
    store().setUser('user-2');
    expect(isCurrent(before)).toBe(false);
  });
});

describe('사용자 · 로그아웃 · 새로고침', () => {
  it('사용자가 바뀌면 보내는 중 · 실행 · 취소 · 대화를 비운다', () => {
    store().openChat(pitchA);
    sendAndAccept('run-1', 'conv-1');
    store().setRunPhase('run-1', 'WAITING_APPROVAL');
    store().beginSend();
    store().beginCancel('run-1');
    expect(store().pending).not.toBeNull();
    store().setUser('user-2');
    expect(store()).toMatchObject({
      userId: 'user-2',
      open: false,
      conversationId: null,
      pending: null,
      run: null,
      cancelling: null,
      scope: null,
    });
  });

  it('새로고침하면 대화 · 범위 · 실행을 되살리되 커서는 0 부터, 실행 상태를 읽을 때까지 잠근다', () => {
    store().openChat(pitchA);
    sendAndAccept('run-1', 'conv-1');
    store().setRunPhase('run-1', 'WAITING_APPROVAL');
    store().advanceCursor('run-1', 5);
    reload('user-1');
    expect(store()).toMatchObject({
      conversationId: 'conv-1',
      scope: pitchA,
      syncing: true,
      run: { runId: 'run-1', afterSeq: 0 },
    });
    expect(canChangeScope(store())).toBe(false);
    store().resolveConversation(chatTicket(), { runId: 'run-1', phase: 'WAITING_APPROVAL' });
    expect([store().syncing, canSend(store())]).toEqual([false, true]);
  });

  it('보내는 중에 새로고침하면 미확정으로 되살려 같은 sendId 로 다시 보낸다', () => {
    store().openChat(pitchA);
    const ticket = store().beginSend()!;
    reload('user-1');
    expect(store().pending).toMatchObject({ sendId: ticket.sendId, unconfirmed: true });
    expect(canSend(store())).toBe(false);
    expect(store().beginRun(pendingSendTicket()!, 'run-1', 'conv-1')).toBe(true);
    expect(store()).toMatchObject({ conversationId: 'conv-1', scope: pitchA, pending: null });
  });

  it('다른 사용자의 사본은 되살리지 않고 지운다', () => {
    store().openChat(pitchA);
    sendAndAccept('run-1', 'conv-1');
    store().setUser('user-2');
    expect(sessionStorage.getItem('pitchcoach.chat.user-1')).toBeNull();
    store().setUser('user-1');
    expect(store().conversationId).toBeNull();
  });

  it('망가진 사본은 버리고 빈 상태로 시작한다', () => {
    sessionStorage.setItem(
      'pitchcoach.chat.user-3',
      JSON.stringify({ conversationId: 7, scope: 'x', run: { runId: 1 }, pending: { sendId: '' } }),
    );
    store().setUser('user-3');
    expect(store()).toMatchObject({ conversationId: null, run: null, pending: null, scope: null });
  });

  it('로그아웃 신호를 받으면 비우고 세대를 한 번 올리고 사본도 지운다', async () => {
    const unbind = bindChatToSession();
    store().openChat(pitchA);
    sendAndAccept('run-1', 'conv-1');
    const generation = store().generation;
    announceSignedOut();
    await settle();
    expect(store()).toMatchObject({ userId: null, conversationId: null, run: null });
    expect(store().generation).toBe(generation + 1);
    expect(sessionStorage.length).toBe(0);
    unbind();
  });

  it('다른 탭의 로그아웃 신호로도 비운다', async () => {
    const unbind = bindChatToSession();
    store().openChat(pitchA);
    sendAndAccept('run-1', 'conv-1');
    const other = new BroadcastChannel('pitchcoach-session');
    other.postMessage({ type: 'signed-out', tab: 'other-tab' });
    other.close();
    await settle();
    expect(store()).toMatchObject({ userId: null, conversationId: null });
    unbind();
  });
});

describe('모달에 가려질 때', () => {
  it('모달이 열리면 대화창을 가리고, 닫히면 이전 열림 상태로 돌린다', () => {
    store().openChat(pitchA);
    store().coverByModal();
    expect(store().open).toBe(false);
    store().uncoverByModal();
    expect(store().open).toBe(true);
  });

  it('가려진 동안 열거나 닫으면, 모달이 닫힌 뒤 그 상태가 된다', () => {
    store().coverByModal();
    store().openChat(pitchA);
    expect(store().open).toBe(false);
    store().uncoverByModal();
    expect(store().open).toBe(true);
    store().coverByModal();
    store().closeChat();
    store().uncoverByModal();
    expect(store().open).toBe(false);
  });

  it('모달이 겹치면 모두 닫힐 때까지 가린다', () => {
    store().openChat(pitchA);
    store().coverByModal();
    store().coverByModal();
    store().uncoverByModal();
    expect(store().open).toBe(false);
    store().uncoverByModal();
    expect(store().open).toBe(true);
  });

  it('가려진 동안 사용자가 바뀌어도 모달 수는 그대로이고, 모달이 닫혀도 대화창은 닫혀 있다', () => {
    store().openChat(pitchA);
    store().coverByModal();
    store().setUser('user-2');
    expect(store().coveredBy).toBe(1);
    store().uncoverByModal();
    expect(store().open).toBe(false);
  });
});
