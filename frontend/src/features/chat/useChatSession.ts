import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchTakeReport } from '@/shared/api/report';
import { takeScreenSnapshot } from '@/shared/lib/chatScreen';
import type { ChatMessage, ChatPage } from '@/types/chat';
import { useChatAvailability, type ChatAvailability } from './chatAvailability';
import { fetchMessages } from './chatApi';
import type { LiveProgress } from './ChatAnswer';
import type { ShownClaim } from './chatResponse';
import type { ChatRouteInfo } from './chatRoutes';
import { WHOLE_SCOPE, useChatStore, type ScreenScope } from './chatStore';
import { createChatTransport, type ChatTransport, type TransportSignal } from './chatTransport';
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
  | { kind: 'criteriaTooLong' };

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
}

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
  // 큐는 세션마다 하나. 화면에서 빠질 때 비우기만 하고 닫지 않습니다 — 엄격 모드에서 다시 붙어도 쓰게
  const [typing] = useState(() =>
    createTypingQueue<ShownClaim>({ onLineDone: (line) => setAnnounce(line.item.text) }),
  );
  /** 큐가 들고 있는 실행 — 신호 처리 안에서 곧바로 견줍니다 */
  const liveRun = useRef<string | null>(null);
  useEffect(() => () => typing.reset(), [typing]);
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
  useEffect(() => {
    if (!on) return;
    const created = createChatTransport();
    transport.current = created;
    // 듣기를 먼저 겁니다 — `resume()` 이 되살린 보내기의 미확정을 곧바로 알립니다
    const off = created.subscribe((signal) =>
      handleSignal(signal, {
        markDisabled,
        setNotice,
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
        reread: (id, then) =>
          void queryClient.invalidateQueries({ queryKey: messagesKey(userId, id) }).then(then),
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
    if (runId && conversationId)
      void queryClient.invalidateQueries({ queryKey: messagesKey(userId, conversationId) });
  }, [runId, conversationId, queryClient, userId]);

  // ── 메시지 목록 ───────────────────────────────────────
  const messages = useQuery({
    queryKey: messagesKey(userId, conversationId),
    queryFn: () => fetchMessages(conversationId!),
    enabled: on && open && conversationId !== null,
  });

  // ── 보내기 ───────────────────────────────────────────
  const send = useCallback(
    async (text: string) => {
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
        choice: null,
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

  return {
    availability,
    retryAvailability,
    screenScope,
    scopeFailed: info.needsTake && take.isError && !take.data,
    retryScope: () => void take.refetch(),
    messages: messages.data?.messages ?? [],
    progress: progress && progress.conversationId === conversationId ? progress : null,
    typing,
    announce,
    notice,
    send,
    retryUnconfirmed,
    cancel,
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
      h.setNotice({ kind: 'sendFailed', code: signal.code });
      return;
    case 'sessionExpired':
      h.setNotice({ kind: 'sessionExpired' });
      return;
    default:
      return;
  }
}
