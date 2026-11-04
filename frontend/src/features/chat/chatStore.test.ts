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
} from './chatStore';

const store = () => useChatStore.getState();
const pitchA: ChatScope = { pitchId: 'pitch-a', title: '졸업 발표' };
const pitchB: ChatScope = { pitchId: 'pitch-b', title: '중간 발표' };

beforeEach(() => {
  sessionStorage.clear();
  store().signOut();
  store().setUser('user-1');
});

describe('열기 · 범위', () => {
  it('처음 열면 연 화면의 범위로, 대화를 시작한 뒤에는 화면을 옮겨도 범위가 그대로다', () => {
    store().openChat(pitchA);
    expect(store()).toMatchObject({ open: true, scope: pitchA, conversationId: null });

    store().beginRun('run-1', 'conv-1');
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

  it('실행 중에는 범위 칩 · 새 대화 · 대화 고르기를 막는다', () => {
    store().openChat(pitchA);
    store().beginRun('run-1', 'conv-1');
    expect(store().chooseScope(pitchB)).toBe(false);
    expect(store().startNewConversation(pitchB)).toBe(false);
    expect(store().selectConversation('conv-2', pitchB)).toBe(false);
    expect(store()).toMatchObject({ scope: pitchA, conversationId: 'conv-1' });

    store().endRun('run-1');
    expect(store().chooseScope(pitchB)).toBe(true);
  });

  it('서버가 확정한 범위는 그 대화를 보고 있을 때만 칩에 반영한다', () => {
    store().selectConversation('conv-1', WHOLE_SCOPE);
    store().applyServerScope('conv-other', pitchB);
    expect(store().scope).toEqual(WHOLE_SCOPE);
    store().applyServerScope('conv-1', pitchB);
    expect(store().scope).toEqual(pitchB);
  });

  it("'기억 없이' 는 새 대화에서만 고를 수 있다", () => {
    expect(store().setMemoryOff(true)).toBe(true);
    store().beginRun('run-1', 'conv-1');
    expect(store().setMemoryOff(false)).toBe(false);
    expect(store().memoryOff).toBe(true);
  });
});

describe('실행 · 커서', () => {
  it('커서는 그 실행에서 앞으로만 간다 — 같은 순번 · 뒤로 · 다른 실행은 무시', () => {
    store().beginRun('run-1', 'conv-1');
    expect(store().advanceCursor('run-1', 3)).toBe(true);
    expect(store().advanceCursor('run-1', 3)).toBe(false);
    expect(store().advanceCursor('run-1', 2)).toBe(false);
    expect(store().advanceCursor('run-2', 9)).toBe(false);
    expect(store().run).toEqual({ runId: 'run-1', conversationId: 'conv-1', afterSeq: 3 });
  });

  it('다른 실행의 종결은 지금 실행을 지우지 않는다', () => {
    store().beginRun('run-2', 'conv-1');
    store().endRun('run-1');
    expect(store().run?.runId).toBe('run-2');
  });
});

describe('지금 화면의 응답인지', () => {
  it('세대 · 대화 · 실행이 같을 때만 지금 것이다', () => {
    store().selectConversation('conv-1', WHOLE_SCOPE);
    const forList = chatTicket();
    store().beginRun('run-1', 'conv-1');
    const forRun = chatTicket();
    expect([isCurrent(forList), isCurrent(forRun)]).toEqual([true, true]);

    store().endRun('run-1');
    store().beginRun('run-2', 'conv-1');
    expect(isCurrent(forRun)).toBe(false);

    store().endRun('run-2');
    store().selectConversation('conv-2', WHOLE_SCOPE);
    expect(isCurrent(forList)).toBe(false);
  });

  it('사용자가 바뀌면 세대가 올라 앞 세대의 응답을 버린다', () => {
    const before = chatTicket();
    store().setUser('user-2');
    expect(isCurrent(before)).toBe(false);
  });
});

describe('사용자 · 로그아웃', () => {
  it('사용자가 바뀌면 대화 · 실행을 비운다', () => {
    store().openChat(pitchA);
    store().beginRun('run-1', 'conv-1');
    store().setUser('user-2');
    expect(store()).toMatchObject({
      userId: 'user-2',
      open: false,
      conversationId: null,
      run: null,
      scope: WHOLE_SCOPE,
    });
  });

  it('같은 사용자로 새로고침하면 대화 · 범위 · 실행 커서를 되살린다', () => {
    store().openChat(pitchA);
    store().beginRun('run-1', 'conv-1');
    store().advanceCursor('run-1', 4);
    // 새로고침: 메모리는 사라지고 sessionStorage 만 남습니다
    useChatStore.setState({ userId: null, conversationId: null, run: null, scope: WHOLE_SCOPE });
    store().setUser('user-1');
    expect(store()).toMatchObject({
      conversationId: 'conv-1',
      scope: pitchA,
      run: { runId: 'run-1', conversationId: 'conv-1', afterSeq: 4 },
    });
  });

  it('다른 사용자의 사본은 되살리지 않고 지운다', () => {
    store().beginRun('run-1', 'conv-1');
    store().setUser('user-2');
    expect(sessionStorage.getItem('pitchcoach.chat.user-1')).toBeNull();
    store().setUser('user-1');
    expect(store().conversationId).toBeNull();
  });

  it('망가진 사본은 버리고 빈 상태로 시작한다', () => {
    sessionStorage.setItem(
      'pitchcoach.chat.user-3',
      JSON.stringify({ conversationId: 7, run: { runId: 'r', afterSeq: -1 } }),
    );
    store().setUser('user-3');
    expect(store()).toMatchObject({ conversationId: null, run: null, scope: WHOLE_SCOPE });
  });

  it('로그아웃 신호(이 탭 · 다른 탭)를 받으면 비우고 사본도 지운다', () => {
    const unbind = bindChatToSession();
    store().beginRun('run-1', 'conv-1');
    const generation = store().generation;
    announceSignedOut();
    expect(store()).toMatchObject({ userId: null, conversationId: null, run: null });
    expect(store().generation).toBe(generation + 1);
    expect(sessionStorage.length).toBe(0);
    unbind();
  });
});

describe('모달에 가려질 때', () => {
  it('모달이 열리면 대화창을 가리고, 닫히면 이전 열림 상태로 돌린다', () => {
    store().openChat(pitchA);
    store().coverByModal();
    expect(store().open).toBe(false);
    store().coverByModal();
    store().uncoverByModal();
    expect(store().open).toBe(true);

    store().closeChat();
    store().coverByModal();
    store().uncoverByModal();
    expect(store().open).toBe(false);
  });
});
