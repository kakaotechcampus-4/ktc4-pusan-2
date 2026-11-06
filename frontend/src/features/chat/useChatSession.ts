import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchTakeReport } from '@/shared/api/report';
import { takeScreenSnapshot } from '@/shared/lib/chatScreen';
import type { ChatMessage, ChatPage, ConversationListItem } from '@/types/chat';
import { useChatAvailability, type ChatAvailability } from './chatAvailability';
import { fetchMessages } from './chatApi';
import { conversationsKey } from './chatConversations';
import type { LiveProgress } from './ChatAnswer';
import type { ShownClaim } from './chatResponse';
import type { ChatRouteInfo } from './chatRoutes';
import {
  WHOLE_SCOPE,
  useChatStore,
  type ChatScope,
  type ScreenScope,
  type SendDraft,
} from './chatStore';
import {
  createChatTransport,
  type ChatTransport,
  type DecideResult,
  type TransportSignal,
  type UndoResult,
} from './chatTransport';
import { createTypingQueue, feedProgress, type TypingQueue } from './chatTyping';

/**
 * 대화창 한 세션의 연결 (`docs/ai/features/chat-agent.md` 12-4 F1 · F2 · F3) — 로그인 사용자 하나 · 허용 화면 위에서만 삽니다.
 *
 * - 사용 가능 여부가 켜짐일 때만 통신 계층을 **하나** 만들고, 꺼지거나 화면에서 빠지면 닫습니다.
 *   만든 직후 `resume()` 으로 되살린 대화 · 실행 · 보내던 요청을 이어 갑니다(새로고침 복원).
 * - 처음 범위는 연 화면으로 정합니다. Take 화면은 그 Take 를 조회해 피치를 알기 전까지 "범위 확인 중"(null)입니다.
 * - 보낼 때마다 지금 화면 `page`(`{route, pitch_id, take_id, screen}`)를 싣습니다. 화면 상태는 보내는 순간에만 읽습니다.
 * - 메시지 목록은 서버 값이라 TanStack Query 에 두고, 새 실행이 붙거나 끝나면 다시 읽습니다.
 * - 실행 중에 받은 문장은 글자 효과 큐(`chatTyping`)로 보입니다 — 큐는 지금 실행 하나의 답만 듭니다.
 *   끝나면(`ended` · `lost`) 남은 글자를 다 보이고, 목록을 다시 읽은 뒤 저장된 답으로 바꿉니다(F12 "final 을 받으면 큐를 비우고 최종 답으로").
 */

export type ChatNotice =
  | { kind: 'unconfirmed' }
  | { kind: 'sendFailed'; code: string }
  | { kind: 'sessionExpired' }
  | { kind: 'criteriaTooLong' }
  /** 고른 피치가 없어짐(404) — 범위를 다시 고를 때까지 보내기를 막습니다 (L3-07) */
  | { kind: 'scopeMissing' }
  /** 범위 바꾸기가 받아들여지지 않음 — 칩은 앞 범위로 돌아갔습니다 */
  | { kind: 'scopeFailed' }
  /** 보던 대화가 없어짐 — 새 대화로 옮겼습니다 (L3-35) */
  | { kind: 'conversationMissing' };

/**
 * 접수된 내 말 — 접수(202)로 "보내는 중" 이 풀린 뒤, 목록을 다시 읽어 그 말이 들어올 때까지 이것으로 그립니다.
 * 그 사이 말풍선이 사라졌다 나타나지 않게 합니다
 */
export interface SentEcho {
  runId: string;
  conversationId: string;
  text: string;
}

export const messagesKey = (userId: string, conversationId: string | null) =>
  ['chat', 'messages', userId, conversationId] as const;

export interface ChatSession {
  availability: ChatAvailability;
  retryAvailability: () => void;
  /** 지금 화면이 아는 범위 — null 이면 범위 확인 중 */
  screenScope: ScreenScope;
  /** Take 를 조회하지 못해 범위를 모름 — 다시 시도할 수 있습니다 (L3-06) */
  scopeFailed: boolean;
  retryScope: () => void;
  messages: ChatMessage[];
  /** 더 앞의 메시지가 있음 · 읽기 · 읽는 중 */
  hasEarlier: boolean;
  loadEarlier: () => void;
  loadingEarlier: boolean;
  /** 메시지 목록을 다시 읽습니다 — 읽지 못했을 때 */
  retryMessages: () => void;
  /** 범위를 다시 고를 때까지 보내기를 막음 (L3-07) */
  scopeBlocked: boolean;
  /** 범위 칩 — 새 대화는 바로, 있는 대화는 서버에 바꾸기를 요청합니다 */
  changeScope: (scope: ChatScope) => Promise<boolean>;
  /** [새 대화] — 지금 화면 범위의 새 대화 입력 상태로 */
  startNew: () => void;
  /** 대화 목록에서 고름 */
  selectConversation: (item: ConversationListItem) => void;
  /** 메시지 목록을 처음 읽는 중 — 빈 대화 안내를 미룹니다 */
  messagesLoading: boolean;
  /** 메시지 목록을 읽지 못함 — 빈 대화로 보이지 않게 합니다 */
  messagesFailed: boolean;
  /** 접수됐지만 아직 목록에 없는 내 말 */
  sentEcho: SentEcho | null;
  /** 지금 실행의 진행 — 보고 있는 대화의 것만 */
  progress: LiveProgress | null;
  /** 지금 실행의 문장 큐 */
  typing: TypingQueue<ShownClaim>;
  /** 화면 읽기 프로그램에 알릴 마지막 문장 — 다 보일 때마다 하나씩 (L3-37) */
  announce: string;
  notice: ChatNotice | null;
  /** 보냅니다 — 접수됐거나(미확정 포함) 같은 요청을 다시 확인할 수 있으면 true(입력을 비워도 됨) */
  send: (text: string) => Promise<boolean>;
  retryUnconfirmed: () => void;
  /** [취소] — 결과는 신호로 옵니다 */
  cancel: () => void;
  /** 승인 확인 카드의 [승인] · [거절] */
  decide: (
    actionId: string,
    decision: 'APPROVE' | 'REJECT',
    argsHash: string,
  ) => Promise<DecideResult>;
  /** 목표 저장 카드의 [되돌리기] — 되돌렸으면 메시지 목록을 다시 읽습니다("목표를 되돌렸어요.") */
  undo: (runId: string, actionId: string) => Promise<UndoResult>;
  /** 목표 제안의 [이 안으로] — "{고른 안}으로 목표를 잡아 줘" 와 `choice` 를 함께 보냅니다 (12-4 F6) */
  choose: (proposalId: string, option: 'USER' | 'EXPERT') => Promise<boolean>;
}

/** 목표 제안에서 고른 안의 이름 — 보낼 글에 씁니다 */
const CHOSEN_LABEL = { USER: '사용자 안', EXPERT: '추천 안' } as const;

export function useChatSession(userId: string, info: ChatRouteInfo): ChatSession {
  const queryClient = useQueryClient();
  const {
    status: availability,
    retry: retryAvailability,
    markDisabled,
  } = useChatAvailability(userId);
  const transport = useRef<ChatTransport | null>(null);
  const [notice, setNotice] = useState<ChatNotice | null>(null);
  const [progress, setProgress] = useState<LiveProgress | null>(null);
  const [announce, setAnnounce] = useState('');
  const [sentEcho, setSentEcho] = useState<SentEcho | null>(null);
  const [scopeBlocked, setScopeBlocked] = useState(false);
  /** 지금 화면 범위 — 신호 처리(대화 없어짐 → 새 대화)에서 씁니다 */
  const screenScopeRef = useRef<ScreenScope>(null);
  // 큐는 세션마다 하나. 화면에서 빠질 때 비우기만 하고 닫지 않습니다 — 엄격 모드에서 다시 붙어도 쓰게
  const [typing] = useState(() =>
    createTypingQueue<ShownClaim>({ onLineDone: (line) => setAnnounce(line.item.text) }),
  );
  /** 큐가 들고 있는 실행 — 신호 처리 안에서 곧바로 견줍니다 */
  const liveRun = useRef<string | null>(null);
  useEffect(() => () => typing.reset(), [typing]);

  // 저장소의 바뀜을 따라갑니다(외부 저장소 구독)
  // - 보내는 중 → 실행이 붙음: 그 말을 목록에 들어올 때까지 붙잡아 둡니다
  // - 대화를 옮김(시도 번호가 바뀜): 앞 대화의 진행 답을 비웁니다. 돌아왔을 때 실행이 진행 중이면 통신 계층이
  //   처음부터 다시 보내(restart) 다시 그려지고, 이미 끝났으면 남지 않습니다
  useEffect(
    () =>
      useChatStore.subscribe((now, before) => {
        if (before.pending && !now.pending && now.run && now.run.runId !== before.run?.runId)
          setSentEcho({
            runId: now.run.runId,
            conversationId: now.run.conversationId,
            text: before.pending.request.text,
          });
        if (now.scope !== before.scope || now.attempt !== before.attempt) setScopeBlocked(false);
        if (now.attempt !== before.attempt) {
          typing.reset();
          liveRun.current = null;
          setProgress(null);
          setSentEcho(null);
          // 앞 대화의 마지막 문장을 화면 읽기 영역에 남기지 않습니다
          setAnnounce('');
        }
      }),
    [typing],
  );
  const conversationId = useChatStore((s) => s.conversationId);
  const runId = useChatStore((s) => s.run?.runId ?? null);
  const open = useChatStore((s) => s.open);

  // ── 범위 ─────────────────────────────────────────────
  const take = useQuery({
    queryKey: ['takes', info.takeId, 'report'],
    queryFn: () => fetchTakeReport(info.takeId!),
    enabled: info.needsTake && info.takeId !== null,
    // Take 의 피치는 바뀌지 않습니다
    staleTime: Infinity,
  });
  const takeScope =
    take.data && take.data.take_id === info.takeId
      ? { pitchId: take.data.pitch_id, title: take.data.pitch_title }
      : null;
  const screenScope = screenScopeOf(info, takeScope);
  useEffect(() => {
    screenScopeRef.current = screenScope;
  });
  const scopePitchId = screenScope?.pitchId;
  const scopeTitle = screenScope?.title;
  const scopeKnown = screenScope !== null;

  // 열린 동안 범위를 아직 정하지 않았으면 화면을 따라갑니다 — Take 조회가 끝나 피치를 알게 된 때 포함.
  // 정한 뒤(보내기 · 칩 · 지난 대화)에는 저장소가 덮어쓰지 않습니다
  useEffect(() => {
    if (!open) return;
    useChatStore
      .getState()
      .syncScreenScope(
        scopeKnown ? { pitchId: scopePitchId ?? null, title: scopeTitle ?? null } : null,
      );
  }, [open, scopeKnown, scopePitchId, scopeTitle]);

  // ── 통신 계층 ─────────────────────────────────────────
  const on = availability === 'on';
  // 꺼지면 닫습니다 — 다시 켜졌을 때 패널이 저절로 열려 초점을 빼앗지 않게(숨는 화면과 같은 규칙)
  useEffect(() => {
    if (availability === 'off' && useChatStore.getState().open) useChatStore.getState().closeChat();
  }, [availability]);
  useEffect(() => {
    if (!on) return;
    const created = createChatTransport();
    transport.current = created;
    // 듣기를 먼저 겁니다 — `resume()` 이 되살린 보내기의 미확정을 곧바로 알립니다
    const off = created.subscribe((signal) =>
      handleSignal(signal, {
        markDisabled,
        setNotice,
        blockScope: () => setScopeBlocked(true),
        startNew: () => useChatStore.getState().startNewConversation(screenScopeRef.current),
        rereadConversations: () =>
          void queryClient.invalidateQueries({ queryKey: conversationsKey(userId) }),
        live: {
          take: (signal) => {
            if (liveRun.current !== signal.runId) {
              typing.reset();
              liveRun.current = signal.runId;
            }
            feedProgress(typing, signal);
            setProgress({
              runId: signal.runId,
              conversationId: signal.conversationId,
              plan: signal.plan,
              trace: signal.trace,
              contractError: signal.contractError,
              progressDropped: signal.progressDropped,
              cards: signal.cards,
            });
          },
          finish: (runId) => {
            if (liveRun.current === runId) typing.finish();
          },
          clear: (runId) => {
            if (liveRun.current !== runId) return;
            typing.reset();
            liveRun.current = null;
            setProgress(null);
          },
        },
        // 다시 읽기가 실패하면(`invalidateQueries` 는 실패해도 끝납니다) 받은 답을 지우지 않습니다 — 목록에 답이
        // 들어오면 화면이 진행 답을 숨기므로 남겨 둬도 두 번 보이지 않습니다
        reread: (id, then) =>
          void queryClient.invalidateQueries({ queryKey: messagesKey(userId, id) }).then(() => {
            if (queryClient.getQueryState(messagesKey(userId, id))?.status !== 'error') then();
          }),
      }),
    );
    created.resume();
    return () => {
      off();
      created.dispose();
      if (transport.current === created) transport.current = null;
    };
  }, [on, queryClient, userId, markDisabled, typing]);

  // 새 실행이 붙으면 사용자 말이 저장돼 있으니 목록을 다시 읽습니다
  useEffect(() => {
    if (!runId || !conversationId) return;
    void queryClient.invalidateQueries({ queryKey: messagesKey(userId, conversationId) });
    void queryClient.invalidateQueries({ queryKey: conversationsKey(userId) });
  }, [runId, conversationId, queryClient, userId]);

  // ── 메시지 목록 ───────────────────────────────────────
  // 최근 한 페이지부터, [이전 대화 더 보기]로 앞 페이지를 이어 읽습니다(`next_before_seq`)
  const messages = useInfiniteQuery({
    queryKey: messagesKey(userId, conversationId),
    queryFn: ({ pageParam }) => fetchMessages(conversationId!, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (page) => page.next_before_seq ?? undefined,
    enabled: on && open && conversationId !== null,
  });
  // 페이지는 최근 것부터 쌓이고, 한 페이지 안은 오래된 순입니다
  const shown = messages.data
    ? [...messages.data.pages].reverse().flatMap((page) => page.messages)
    : [];

  // ── 보내기 ───────────────────────────────────────────
  const send = useCallback(
    async (text: string, choice: SendDraft['choice'] = null) => {
      const current = transport.current;
      if (!current) return false;
      const snapshot = takeScreenSnapshot(info.route);
      if (!snapshot.ok) {
        setNotice({ kind: 'criteriaTooLong' });
        return false;
      }
      const page: ChatPage = {
        route: info.route,
        pitch_id: scopePitchId ?? null,
        take_id: info.takeId,
        screen: snapshot.screen,
      };
      setNotice(null);
      const result = await current.send({
        text,
        page,
        again_of_message_id: null,
        choice,
      });
      return result === 'accepted' || result === 'unconfirmed';
    },
    [info.route, info.takeId, scopePitchId],
  );

  const retryUnconfirmed = useCallback(() => {
    const current = transport.current;
    if (!current) return;
    setNotice(null);
    void current.retryUnconfirmed();
  }, []);

  const cancel = useCallback(() => void transport.current?.cancel(), []);

  const changeScope = useCallback<ChatSession['changeScope']>(async (scope) => {
    const current = transport.current;
    if (!current) return false;
    setNotice(null);
    return current.changeScope(scope);
  }, []);

  const startNew = useCallback(() => {
    setNotice(null);
    useChatStore.getState().startNewConversation(screenScopeRef.current);
  }, []);

  const selectConversation = useCallback<ChatSession['selectConversation']>((item) => {
    setNotice(null);
    useChatStore.getState().selectConversation(item.conversation_id, {
      pitchId: item.scope_pitch_id,
      title: item.scope_pitch_title,
    });
  }, []);

  const decide = useCallback<ChatSession['decide']>(
    async (actionId, decision, argsHash) =>
      transport.current ? transport.current.decide(actionId, decision, argsHash) : 'stale',
    [],
  );

  const undo = useCallback<ChatSession['undo']>(
    async (runId, actionId) => {
      const current = transport.current;
      if (!current) return 'stale';
      const result = await current.undo(runId, actionId);
      const id = useChatStore.getState().conversationId;
      if (result === 'undone' && id)
        void queryClient.invalidateQueries({ queryKey: messagesKey(userId, id) });
      return result;
    },
    [queryClient, userId],
  );

  const choose = useCallback<ChatSession['choose']>(
    (proposalId, option) =>
      send(`${CHOSEN_LABEL[option]}으로 목표를 잡아 줘`, { proposal_id: proposalId, option }),
    [send],
  );

  return {
    availability,
    retryAvailability,
    screenScope,
    scopeFailed: info.needsTake && take.isError && !take.data,
    retryScope: () => void take.refetch(),
    messages: shown,
    hasEarlier: messages.hasNextPage,
    loadEarlier: () => void messages.fetchNextPage(),
    loadingEarlier: messages.isFetchingNextPage,
    retryMessages: () => void messages.refetch(),
    scopeBlocked,
    changeScope,
    startNew,
    selectConversation,
    messagesLoading: messages.isPending && messages.fetchStatus === 'fetching',
    messagesFailed: messages.isError,
    sentEcho: sentEcho && sentEcho.conversationId === conversationId ? sentEcho : null,
    progress: progress && progress.conversationId === conversationId ? progress : null,
    typing,
    announce,
    notice,
    send,
    retryUnconfirmed,
    cancel,
    decide,
    undo,
    choose,
  };
}

/** 연 화면의 처음 범위 (12-4 F2 "처음 범위" 표) */
function screenScopeOf(info: ChatRouteInfo, takeScope: ScreenScope): ScreenScope {
  if (info.needsTake) return takeScope;
  if (info.pitchId) return { pitchId: info.pitchId, title: null };
  return WHOLE_SCOPE;
}

interface SignalHandlers {
  markDisabled: () => void;
  setNotice: (notice: ChatNotice | null) => void;
  /** 범위를 다시 고를 때까지 보내기를 막습니다 */
  blockScope: () => void;
  /** 지금 화면 범위의 새 대화로 */
  startNew: () => void;
  /** 대화 목록을 다시 읽습니다 — 제목 · 마지막 시각 · 최신 아님 · 범위가 바뀌었을 수 있습니다 */
  rereadConversations: () => void;
  live: {
    /** 진행 신호를 큐에 넣습니다 — 다른 실행이면 큐를 비우고 새로 */
    take: (signal: Extract<TransportSignal, { type: 'progress' }>) => void;
    /** 남은 글자를 다 보입니다 */
    finish: (runId: string) => void;
    /** 저장된 답으로 바꾼 뒤 비웁니다 */
    clear: (runId: string) => void;
  };
  /** 그 대화의 메시지 목록을 다시 읽고, 다 읽으면 `then` */
  reread: (conversationId: string, then: () => void) => void;
}

/** 통신 계층의 신호 — 안내 전체(F8)는 8-5 에서 채웁니다. 여기서는 연결에 꼭 필요한 것만 받습니다 */
function handleSignal(signal: TransportSignal, h: SignalHandlers) {
  switch (signal.type) {
    case 'progress':
      h.live.take(signal);
      return;
    case 'ended':
      h.rereadConversations();
      h.live.finish(signal.runId);
      h.reread(signal.conversationId, () => h.live.clear(signal.runId));
      return;
    case 'lost': {
      // 실행을 더 읽을 수 없으니 저장된 답을 찾으러 지금 대화의 목록을 다시 읽습니다 (L3-21)
      const conversationId = useChatStore.getState().conversationId;
      h.live.finish(signal.runId);
      if (conversationId) h.reread(conversationId, () => h.live.clear(signal.runId));
      else h.live.clear(signal.runId);
      return;
    }
    case 'disabled':
      h.markDisabled();
      return;
    case 'unconfirmed':
      h.setNotice({ kind: 'unconfirmed' });
      return;
    case 'sendFailed':
      // 범위의 피치가 없어짐 — 범위를 다시 고르게 합니다(L3-07). BE 는 대화 · 피치 · 제안의 404 를 같은 코드로 줘서
      // 범위가 피치일 때만 이 안내로 봅니다
      if (signal.code === 'NOT_FOUND' && useChatStore.getState().scope?.pitchId) {
        h.blockScope();
        h.setNotice({ kind: 'scopeMissing' });
        return;
      }
      h.setNotice({ kind: 'sendFailed', code: signal.code });
      return;
    case 'scopeFailed':
      h.setNotice(signal.code === 'NOT_FOUND' ? { kind: 'scopeMissing' } : { kind: 'scopeFailed' });
      return;
    case 'conversationMissing':
      h.startNew();
      h.setNotice({ kind: 'conversationMissing' });
      h.rereadConversations();
      return;
    case 'sessionExpired':
      h.setNotice({ kind: 'sessionExpired' });
      return;
    default:
      return;
  }
}
