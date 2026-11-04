// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';
import { announceSignedOut } from '@/shared/lib/sessionSignal';
import {
  WHOLE_SCOPE,
  bindChatToSession,
  chatTicket,
  isCurrent,
  useChatStore,
  type ChatScope,
  type ChatTicket,
} from './chatStore';

const store = () => useChatStore.getState();
const pitchA: ChatScope = { pitchId: 'pitch-a', title: '졸업 발표' };
const pitchB: ChatScope = { pitchId: 'pitch-b', title: '중간 발표' };

/** 보내기 → 접수까지 — 통신 계층이 할 순서 */
function sendAndAccept(runId: string, conversationId: string) {
  const ticket = store().beginSend()!;
  expect(ticket).not.toBeNull();
  expect(store().beginRun(ticket, runId, conversationId)).toBe(true);
  return ticket;
}

/** 새로고침: 메모리는 사라지고 sessionStorage 만 남습니다 */
function reload(userId: string) {
  useChatStore.setState({ userId: null });
  store().setUser(userId);
}

beforeEach(() => {
  sessionStorage.clear();
  store().signOut();
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

    store().chooseScope(WHOLE_SCOPE);
    store().closeChat();
    store().openChat(pitchA);
    expect(store().scope).toEqual(WHOLE_SCOPE);
  });

  it('범위를 모르면 "확인 중"(null)으로 두고 보내지 않는다 — 알게 되면 맞춘 뒤 보낼 수 있다', () => {
    store().openChat(null);
    expect(store().scope).toBeNull();
    expect(store().beginSend()).toBeNull();

    store().syncScreenScope(pitchA);
    expect(store().scope).toEqual(pitchA);
    expect(store().beginSend()).not.toBeNull();
  });

  it('범위를 정한 뒤에는 화면이 알린 범위로 덮어쓰지 않는다', () => {
    store().openChat(pitchA);
    store().chooseScope(pitchB);
    store().syncScreenScope(pitchA);
    expect(store().scope).toEqual(pitchB);
  });

  it('서버가 확정한 범위는 그 대화를 보고 있을 때만 칩에 반영한다', () => {
    store().selectConversation('conv-1', WHOLE_SCOPE);
    store().applyServerScope('conv-other', pitchB);
    expect(store().scope).toEqual(WHOLE_SCOPE);
    store().applyServerScope('conv-1', pitchB);
    expect(store().scope).toEqual(pitchB);
  });

  it("'기억 없이' 는 새 대화에서만 고를 수 있다", () => {
    store().openChat(pitchA);
    expect(store().setMemoryOff(true)).toBe(true);
    sendAndAccept('run-1', 'conv-1');
    expect(store().setMemoryOff(false)).toBe(false);
    expect(store().memoryOff).toBe(true);
  });
});

describe('잠금 — 보내기 · 범위 칩', () => {
  beforeEach(() => store().openChat(pitchA));

  it('보내기를 시작하면 접수 전에도 바로 잠근다 — 두 번 보내기 · 범위 칩을 막는다', () => {
    const ticket = store().beginSend();
    expect(ticket).not.toBeNull();
    expect(store().beginSend()).toBeNull();
    expect(store().chooseScope(pitchB)).toBe(false);

    store().failSend(ticket!);
    expect(store().beginSend()).not.toBeNull();
  });

  it('대기 · 실행 중에는 보내기와 칩을 막고, 승인 대기에서는 보내기만 허용한다', () => {
    sendAndAccept('run-1', 'conv-1');
    expect(store().beginSend()).toBeNull();
    store().setRunPhase('run-1', 'RUNNING');
    expect(store().beginSend()).toBeNull();
    expect(store().chooseScope(pitchB)).toBe(false);

    store().setRunPhase('run-1', 'WAITING_APPROVAL');
    expect(store().chooseScope(pitchB)).toBe(false);
    const next = store().beginSend();
    expect(next).not.toBeNull();
    // 승인 대기 중 보낸 새 메시지는 BE 가 앞 확인을 취소하고 새 실행으로 받습니다
    expect(store().beginRun(next!, 'run-2', 'conv-1')).toBe(true);
    expect(store().run).toMatchObject({ runId: 'run-2', phase: 'QUEUED' });
  });

  it('실행 중에도 새 대화 · 다른 대화로 옮길 수 있다 — 앞 실행은 놓고, 늦은 응답은 버린다', () => {
    sendAndAccept('run-1', 'conv-1');
    store().setRunPhase('run-1', 'RUNNING');
    const poll = chatTicket();

    store().selectConversation('conv-2', pitchB);
    expect(store()).toMatchObject({ conversationId: 'conv-2', run: null });
    expect(isCurrent(poll)).toBe(false);

    store().startNewConversation(pitchA);
    expect(store()).toMatchObject({ conversationId: null, run: null, scope: pitchA });
    expect(store().beginSend()).not.toBeNull();
  });

  it('보내는 사이 새 대화를 시작하면 늦게 온 접수는 붙지 않는다', () => {
    const ticket = store().beginSend()!;
    store().startNewConversation(pitchB);
    expect(isCurrent(ticket)).toBe(false);
    expect(store().beginRun(ticket, 'run-late', 'conv-late')).toBe(false);
    expect(store()).toMatchObject({
      run: null,
      conversationId: null,
      scope: pitchB,
      sending: false,
    });
  });

  it('로그아웃 뒤 늦게 온 접수는 붙지 않는다', () => {
    const ticket = store().beginSend()!;
    store().signOut();
    expect(store().beginRun(ticket, 'run-late', 'conv-late')).toBe(false);
    expect(store().run).toBeNull();
  });
});

describe('실행 · 커서', () => {
  beforeEach(() => store().openChat(pitchA));

  it('커서는 그 실행에서 앞으로만 간다 — 같은 순번 · 뒤로 · 다른 실행은 무시', () => {
    sendAndAccept('run-1', 'conv-1');
    expect(store().advanceCursor('run-1', 3)).toBe(true);
    expect(store().advanceCursor('run-1', 3)).toBe(false);
    expect(store().advanceCursor('run-1', 2)).toBe(false);
    expect(store().advanceCursor('run-2', 9)).toBe(false);
    expect(store().run).toMatchObject({ runId: 'run-1', afterSeq: 3 });
  });

  it('다른 실행의 종결 · 단계는 지금 실행을 바꾸지 않는다', () => {
    sendAndAccept('run-2', 'conv-1');
    store().endRun('run-1');
    store().setRunPhase('run-1', 'WAITING_APPROVAL');
    expect(store().run).toMatchObject({ runId: 'run-2', phase: 'QUEUED' });
  });
});

describe('지금 화면의 응답인지', () => {
  beforeEach(() => store().openChat(pitchA));

  it('세대 · 시도 · 대화 · 실행이 같을 때만 지금 것이다', () => {
    store().selectConversation('conv-1', WHOLE_SCOPE);
    const forList = chatTicket();
    sendAndAccept('run-1', 'conv-1');
    const forRun = chatTicket();
    expect([isCurrent(forList), isCurrent(forRun)]).toEqual([true, true]);

    store().endRun('run-1');
    sendAndAccept('run-2', 'conv-1');
    expect([isCurrent(forList), isCurrent(forRun)]).toEqual([true, false]);

    store().selectConversation('conv-2', WHOLE_SCOPE);
    expect(isCurrent(forList)).toBe(false);
  });

  it('같은 대화를 다시 골라도 시도가 올라 앞의 응답을 버린다', () => {
    store().selectConversation('conv-1', WHOLE_SCOPE);
    const before: ChatTicket = chatTicket();
    store().selectConversation('conv-1', WHOLE_SCOPE);
    expect(isCurrent(before)).toBe(false);
  });

  it('사용자가 바뀌면 세대가 올라 앞 세대의 응답을 버린다', () => {
    const before = chatTicket();
    store().setUser('user-2');
    expect(isCurrent(before)).toBe(false);
  });
});

describe('사용자 · 로그아웃 · 새로고침', () => {
  it('사용자가 바뀌면 대화 · 실행 · 보내는 중을 비운다', () => {
    store().openChat(pitchA);
    sendAndAccept('run-1', 'conv-1');
    store().beginSend();
    store().setUser('user-2');
    expect(store()).toMatchObject({
      userId: 'user-2',
      open: false,
      conversationId: null,
      run: null,
      sending: false,
      scope: null,
    });
  });

  it('새로고침하면 대화 · 범위 · 실행을 되살리되 커서는 0 부터 — 받은 문장을 처음부터 다시 받는다', () => {
    store().openChat(pitchA);
    sendAndAccept('run-1', 'conv-1');
    store().setRunPhase('run-1', 'WAITING_APPROVAL');
    store().advanceCursor('run-1', 5);
    reload('user-1');
    expect(store()).toMatchObject({
      conversationId: 'conv-1',
      scope: pitchA,
      run: { runId: 'run-1', conversationId: 'conv-1', afterSeq: 0 },
    });
  });

  it('되살린 실행은 단계를 모르니 끝나기 전으로 본다 — 첫 조회가 단계를 맞추기 전까지 범위 칩을 막는다', () => {
    store().openChat(pitchA);
    sendAndAccept('run-1', 'conv-1');
    reload('user-1');
    expect(store().run?.phase).toBe('RUNNING');
    expect(store().chooseScope(pitchB)).toBe(false);
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
      JSON.stringify({ conversationId: 7, scope: 'x', run: { runId: 1 } }),
    );
    store().setUser('user-3');
    expect(store()).toMatchObject({ conversationId: null, run: null, scope: null });
  });

  it('로그아웃 신호를 받으면 비우고 세대를 한 번 올리고 사본도 지운다', async () => {
    const unbind = bindChatToSession();
    store().openChat(pitchA);
    sendAndAccept('run-1', 'conv-1');
    const generation = store().generation;
    announceSignedOut();
    // 채널로 되돌아온 자기 탭 메시지로 두 번 비우지 않는지 — 메시지가 도착할 시간을 줍니다
    await new Promise((resolve) => setTimeout(resolve, 30));
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
    await new Promise((resolve) => setTimeout(resolve, 30));
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
    store().uncoverByModal();
    expect(store().open).toBe(true);
  });
});
