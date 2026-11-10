import {
  Fragment,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
  type UIEvent,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useLocation } from 'react-router';
import { ChatErrorBoundary } from './ChatErrorBoundary';
import { useSession } from '@/shared/api/useSession';
import type { ChatMessage } from '@/types/chat';
import { AGENT_BUBBLE, LiveAnswer, StoredAnswer, WAITING_TEXT } from './ChatAnswer';
import { AskHelp } from './ChatAskHelp';
import type { CardActions } from './ChatCards';
import {
  CONTINUE_TEXT,
  decisionFailedText,
  RUN_IN_PROGRESS_TEXT,
  runNoticeText,
  sendFailedText,
  type EndNotice,
  type RunNotice,
} from './chatNotices';
import {
  CHAT_LAUNCHER_CLASS,
  chatLauncherBottom,
  chatRouteOf,
  type ChatRouteInfo,
} from './chatRoutes';
import { useChatStore } from './chatStore';
import { useCanSend } from './useCanSend';
import { sendLockReason } from './chatLock';
import { LockedReason } from './LockedReason';
import { ChatMemorySettings } from './ChatMemorySettings';
import { MemoryIntro } from './ChatMemoryParts';
import { ConversationList } from './ChatPanelParts';
import { ChatPanelHeader, type PanelView } from './ChatPanelHeader';
import { ChatEmptyState } from './ChatEmptyState';
import { useChatSession, type ChatNotice, type ChatSession } from './useChatSession';

/**
 * 대화형 코치 에이전트의 버튼 · 팝업 (`docs/ai/features/chat-agent.md` 12-4 F1). 라우터 최상위 레이아웃에 있어
 * 화면을 옮겨도 대화가 이어지고, 대화 상태는 저장소(`chatStore`)에 둡니다.
 * 채팅에서 오류가 나면 대화만 닫고 런처 자리에 안내와 [다시 시도]를 둡니다. 화면은 그대로 씁니다 (12-4 F8 · L3-36).
 */
export function ChatAgentHost() {
  const { pathname, search } = useLocation();
  const info = chatRouteOf(pathname, search);
  const hidden = info === null;

  // 숨는 화면으로 가면 패널을 닫습니다. 지연 로딩 중인 본문이 교체돼도 초점을 잃지 않게 합니다.
  useLayoutEffect(() => {
    if (!hidden) return;
    const wasOpen = useChatStore.getState().open;
    useChatStore.getState().closeChat();
    if (!wasOpen) return;

    const content = document.querySelector<HTMLElement>('.chat-layout-content');
    if (!content) return;
    let focusedMain: HTMLElement | null = null;
    const focusDestination = () => {
      // 본문이 살아 있는 동안에는 전사·시계 등 내부 변경에 반응하지 않습니다.
      // 로딩 대체 본문이 교체될 수 있어 초점 직후 observer를 끊지는 않습니다.
      if (focusedMain?.isConnected) return;
      const main =
        content.querySelector<HTMLElement>('#main') ?? content.querySelector<HTMLElement>('main');
      if (!main || main === focusedMain) return;
      // 사용자가 로딩 중 다른 조작부로 초점을 옮겼다면 다시 빼앗지 않습니다.
      if (focusedMain && document.activeElement !== document.body) return;
      if (!main.hasAttribute('tabindex')) main.tabIndex = -1;
      main.focus({ preventScroll: true });
      if (document.activeElement === main) focusedMain = main;
    };
    const observer = new MutationObserver(focusDestination);
    observer.observe(content, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['inert'],
    });
    focusDestination();
    return () => observer.disconnect();
  }, [hidden]);

  return (
    <ChatErrorBoundary onReset={resetChatAfterFailure}>
      <ChatLayer pathname={pathname} info={info} />
    </ChatErrorBoundary>
  );
}

/**
 * [다시 시도] 직전에 저장소를 정리합니다 — 저장소는 React 밖이라 경계만 다시 그리면 같은 상태로 또 던질 수 있습니다.
 * 처음에는 대화창만 닫아 보던 대화 · 보내던 요청 · 실행을 그대로 둡니다. 이 페이지에서 두 번째 실패부터는 [새 대화]와
 * 같은 길로 보던 대화에서 나옵니다 — 서버의 대화는 목록에서 다시 열 수 있고, 늦은 응답은 버립니다
 */
function resetChatAfterFailure(failures: number) {
  const chat = useChatStore.getState();
  chat.closeChat();
  if (failures >= 2) chat.startNewConversation(chat.scope);
}

/** 메시지 글자 수 상한. 서버와 같은 기준(Python `len`, 코드 포인트 수)으로 셉니다 (12-4 F3) */
const MAX_MESSAGE_LENGTH = 2000;
const MOBILE_CHAT_QUERY = '(max-width: 760px)';
const isMobileChat = () =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia(MOBILE_CHAT_QUERY).matches;

function ChatLayer({ pathname, info }: { pathname: string; info: ChatRouteInfo | null }) {
  // 입력은 여기 두어 패널을 닫거나 채팅이 숨는 화면을 지나도 남습니다. 사용자가 바뀌면 비웁니다
  const [draft, setDraft] = useState({ userId: null as string | null, text: '' });

  if (!info) return null;

  return (
    <SignedInOnly>
      {(userId) => (
        <ChatSessionView
          userId={userId}
          info={info}
          pathname={pathname}
          draft={draft.userId === userId ? draft.text : ''}
          setDraft={(text) => setDraft({ userId, text })}
        />
      )}
    </SignedInOnly>
  );
}

/**
 * 채팅을 띄우는 화면에서만 세션을 구독합니다. 재조회가 한 번 실패해도 받아 둔 로그인 정보가 있으면 그대로 둡니다 —
 * 열린 패널이 사라지지 않게 합니다.
 * 저장소에는 로그인 사용자가 **확인됐을 때만** 알립니다 — 확인 중 · 오류일 때 `setUser(null)` 을 부르면 새로고침 복원용
 * 사본까지 지웁니다. 저장소가 그 사용자로 바뀐 뒤에만 그려, 앞 사용자의 대화가 한 번이라도 보이지 않게 합니다
 */
function SignedInOnly({ children }: { children: (userId: string) => ReactNode }) {
  const session = useSession();
  const queryClient = useQueryClient();
  const id = session.data?.id ?? null;
  const signedOut = session.data === null;
  const checkedAt = session.dataUpdatedAt;
  const storeUser = useChatStore((s) => s.userId);
  /** 저장소에 알린 때의 세션 확인 시각 */
  const appliedAt = useRef<number | null>(null);

  useEffect(() => {
    if (!id) {
      if (signedOut) useChatStore.getState().setUser(null);
      return;
    }
    if (storeUser === id) return;
    // 저장소가 비워졌는데(다른 탭의 로그아웃 신호) 세션 값은 그 전에 확인한 것 — 세션을 다시 확인한 뒤에만 다시
    // 알립니다. 같은 계정으로 다시 로그인했으면 그때 다시 보이고, 로그아웃 상태면 숨은 채로 둡니다
    if (storeUser === null && appliedAt.current !== null && appliedAt.current === checkedAt) {
      queryClient.invalidateQueries({ queryKey: ['session'] }).catch(() => undefined);
      return;
    }
    appliedAt.current = checkedAt;
    useChatStore.getState().setUser(id);
  }, [id, signedOut, storeUser, checkedAt, queryClient]);

  return id && storeUser === id ? children(id) : null;
}

interface DraftProps {
  draft: string;
  setDraft: (text: string) => void;
}

function ChatSessionView({
  userId,
  info,
  pathname,
  draft,
  setDraft,
}: {
  userId: string;
  info: ChatRouteInfo;
  pathname: string;
} & DraftProps) {
  const session = useChatSession(userId, info);
  const launcherBottom = chatLauncherBottom(pathname);
  const open = useChatStore((s) => s.open);
  const launcherRef = useRef<HTMLButtonElement>(null);
  /** 런처로 열었음 — 패널은 이때만 입력칸에 초점을 줍니다(꺼졌다 켜짐 · 모달이 닫힘으로 다시 그려질 때는 주지 않음) */
  const [focusRequested, setFocusRequested] = useState(false);
  const focused = useCallback(() => setFocusRequested(false), []);
  const id = useId();
  useRestoreRejected(draft, setDraft);

  // 조회 중 · 꺼짐이면 숨깁니다. 실패가 잇달면 버튼은 보이고 열면 다시 시도를 보입니다 (12-4 F1 "사용 가능 여부")
  if (session.availability === 'checking' || session.availability === 'off') return null;

  const close = () => {
    useChatStore.getState().closeChat();
    // 모바일에서는 패널이 닫힐 때까지 런처가 숨겨져 있어 렌더 완료 뒤 초점을 돌립니다.
    if (isMobileChat()) {
      window.setTimeout(() => {
        if (!useChatStore.getState().open) launcherRef.current?.focus();
      }, 0);
    } else {
      launcherRef.current?.focus();
    }
  };

  return (
    // 런처는 화면 왼쪽 아래의 44px 마스코트 아이콘입니다.
    // 화면별 하단 고정 바를 피하는 여백은 chatLauncherBottom 에서 관리합니다
    <div
      // data-chat-launcher: index.css의 피치 생성 여백 보정, 오류 경계의 초점 복귀 및 테스트가 참조합니다
      data-chat-launcher=""
      style={{ '--chat-launcher-default-bottom': `${launcherBottom}px` } as CSSProperties}
      className={CHAT_LAUNCHER_CLASS}
    >
      {open && (
        <ChatPanel
          id={id}
          userId={userId}
          session={session}
          onClose={close}
          draft={draft}
          setDraft={setDraft}
          focusRequested={focusRequested}
          onFocused={focused}
        />
      )}

      <button
        ref={launcherRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? `${id}-panel` : undefined}
        onClick={() => {
          if (open) return close();
          setFocusRequested(true);
          useChatStore.getState().openChat(session.screenScope);
        }}
        aria-label="새미나이 열기"
        title="새미나이 열기"
        className="relative block h-11 w-11 overflow-hidden rounded-full border-2 border-ink bg-coral-deep shadow-[3px_3px_0_var(--color-ink)] hover:bg-ink focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-coral"
      >
        {/* 앱 아이콘 그림(192px)의 몸통 중심 (82, 104)을 44px 로 줄여 원 안쪽(40px)의 가운데에 둡니다.
            오른쪽 꼬리와 반짝이는 원 밖으로 잘립니다 */}
        <img
          src="/icon-192.png"
          alt=""
          className="absolute left-[1.2px] top-[-3.8px] h-11 w-11 max-w-none"
        />
      </button>
    </div>
  );
}

/**
 * 패널 안에서 초점을 가진 버튼이 사라지면(목록 닫힘 · 다시 시도 · 취소 · 카드 버튼 등) 초점이 body 로 빠져
 * Esc 가 패널에 닿지 않습니다(#818 과 같은 문제, 12-4 "Esc 로 닫는다"). 그때 입력칸으로 되돌립니다.
 *
 * - 버튼이 사라지는 때가 누른 순간 · 응답이 온 뒤 · 실행이 끝난 뒤로 여러 갈래라, 경로마다 막지 않고 패널의 DOM 변화를 지켜봅니다.
 * - 사용자가 패널 밖을 눌러 초점을 놓은 것(놓은 요소가 그대로 있음)은 되돌리지 않습니다
 * - 입력칸이 숨겨져 있으면(기억 설정 화면) 초점을 받지 못하니 패널 자체로 옮깁니다
 */
function useKeepFocusInPanel(
  panelRef: RefObject<HTMLElement | null>,
  inputRef: RefObject<HTMLTextAreaElement | null>,
) {
  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    /** 초점이 패널 안에 있었나 — 패널 밖 요소로 옮기거나, 남아 있는 요소에서 놓으면 false */
    let inside = panel.contains(document.activeElement);
    /** 초점을 놓은 요소 — 브라우저마다 사라질 때 blur 를 주기도 하고 안 주기도 합니다 */
    let left: Element | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onIn = () => {
      inside = true;
      left = null;
    };
    const onOut = (event: FocusEvent) => {
      const to = event.relatedTarget as Node | null;
      if (to && panel.contains(to)) return;
      if (to) {
        inside = false;
        return;
      }
      const from = event.target as Element;
      left = from;
      // 다음 틱에도 놓은 요소가 붙어 있으면 패널 밖(초점 안 가는 곳)을 누른 것입니다.
      // 요소가 사라져 놓친 초점이면 그 전에 MutationObserver 가 rescue 합니다
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (left === from && from.isConnected) inside = false;
      }, 0);
    };
    const rescue = () => {
      const active = document.activeElement;
      if (!inside || (active && active !== document.body)) return;
      // 놓은 요소가 그대로 있으면 사용자가 패널 밖을 누른 것입니다
      if (left?.isConnected) {
        inside = false;
        return;
      }
      const input = inputRef.current;
      if (input && !input.closest('[hidden]')) input.focus();
      else panel.focus();
    };
    const observer = new MutationObserver(rescue);
    observer.observe(panel, { childList: true, subtree: true });
    panel.addEventListener('focusin', onIn);
    panel.addEventListener('focusout', onOut);
    return () => {
      observer.disconnect();
      clearTimeout(timer);
      panel.removeEventListener('focusin', onIn);
      panel.removeEventListener('focusout', onOut);
    };
  }, [panelRef, inputRef]);
}

/**
 * 접수되지 않음이 확정된 보내기(`chatStore.rejected`)의 글을 입력에 되돌립니다 — 그사이 새로 쓴 글이 있으면 둡니다.
 * 첫 보내기뿐 아니라 [다시 확인] · [다시 보내기] · 새로고침으로 되살린 요청도 같습니다. 패널이 닫혀 있어도 되돌립니다
 */
function useRestoreRejected(draft: string, setDraft: (text: string) => void) {
  const latest = useRef({ draft, setDraft });
  useLayoutEffect(() => {
    latest.current = { draft, setDraft };
  });
  useEffect(
    () =>
      useChatStore.subscribe((now, before) => {
        if (!now.rejected || now.rejected === before.rejected) return;
        // 입력을 비운 것이 아직 그려지기 전이면 입력은 보낸 글 그대로입니다 — 그때도 되돌립니다(비우기 뒤에 놓임)
        const draft = latest.current.draft.trim();
        if (draft === '' || draft === now.rejected.text) latest.current.setDraft(now.rejected.text);
      }),
    [],
  );
}

function noticeText(notice: ChatNotice): string {
  switch (notice.kind) {
    case 'unconfirmed':
      return notice.notAccepted
        ? '요청을 접수하지 못했으니 다시 보내 주세요.'
        : '전송 여부를 확인하지 못했으니 다시 확인해 주세요.';
    case 'criteriaTooLong':
      return '평가 기준이 2,000자를 넘어 보낼 수 없어요.';
    case 'screenUnavailable':
      return '현재 입력 상태를 확인하지 못했으니 다시 시도해 주세요.';
    case 'sessionExpired':
      return '로그인이 만료됐으니 다시 로그인해 주세요.';
    case 'scopeMissing':
      return '발표를 찾을 수 없으니 범위를 다시 선택해 주세요.';
    case 'scopeFailed':
      // FE 가 정한 문구 — 문서에 없습니다
      return '범위를 바꾸지 못했으니 다시 시도해 주세요.';
    case 'conversationMissing':
      return '대화를 찾을 수 없어요.';
    case 'sendFailed':
      return sendFailedText(notice.code);
    case 'decisionFailed':
      return decisionFailedText(notice.code);
    case 'runInProgress':
      return RUN_IN_PROGRESS_TEXT;
  }
}

/** 맨 아래에서 이만큼 안이면 "맨 아래를 보고 있음" 으로 봅니다 */
const FOLLOW_SLACK_PX = 48;

/**
 * 새 글(받은 문장 · 글자 효과 · 목록)이 붙으면 맨 아래를 보고 있을 때만 따라 내려갑니다.
 * 글자 효과는 이 컴포넌트를 거치지 않고 그려져서, 그려진 결과(DOM 변화)를 지켜봅니다.
 * 대화 목록을 보는 동안에는 따라가지 않고, 메시지로 돌아오면 맨 아래에서 시작합니다
 */
function useFollowBottom(active: boolean, initialAtTop: boolean) {
  const scroller = useRef<HTMLDivElement>(null);
  /** 맨 아래를 보고 있음 — 이때만 새 글을 따라 내려갑니다. 위로 올려 읽는 중에는 끌어내리지 않습니다 */
  const stick = useRef(true);
  useEffect(() => {
    const el = scroller.current;
    if (!el || !active) return;
    // 새 대화의 첫 안내는 위쪽에서 시작하고, 실제 메시지/전송이 생기면 아래로 따라갑니다.
    stick.current = !initialAtTop;
    if (initialAtTop) el.scrollTop = 0;
    const toBottom = () => {
      if (stick.current) el.scrollTop = el.scrollHeight;
    };
    toBottom();
    const observer = new MutationObserver(toBottom);
    observer.observe(el, { childList: true, subtree: true, characterData: true });
    // 입력칸이든 [다시 묻기] · [이어서 확인] · [다시 보내기] · [이 안으로]든, 보내기가 시작되면 그 말을 보이게 따라갑니다
    const offStore = useChatStore.subscribe((now, before) => {
      if (!before.pending && now.pending) stick.current = true;
    });
    return () => {
      observer.disconnect();
      offStore();
    };
  }, [active, initialAtTop]);
  const onScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
    const el = event.currentTarget;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_SLACK_PX;
  }, []);
  /** 다음 새 글부터 다시 따라갑니다 — 내가 보낸 말은 언제나 보이게 */
  const follow = useCallback(() => {
    stick.current = true;
  }, []);
  return { scroller, onScroll, follow };
}

function resizeChatInput(input: HTMLTextAreaElement) {
  input.style.height = 'auto';
  input.style.height = `${Math.max(40, Math.min(input.scrollHeight, 112))}px`;
}

function ChatPanel({
  id,
  userId,
  session,
  onClose,
  focusRequested,
  onFocused,
  draft,
  setDraft,
}: {
  id: string;
  userId: string;
  session: ChatSession;
  onClose: () => void;
  /** 런처로 연 때만 true — 그때만 입력칸에 초점을 주고 `onFocused` 로 알립니다 */
  focusRequested: boolean;
  onFocused: () => void;
} & DraftProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const memoryButtonRef = useRef<HTMLButtonElement>(null);
  const [mobileOverlay, setMobileOverlay] = useState(isMobileChat);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const media = window.matchMedia(MOBILE_CHAT_QUERY);
    const sync = () => setMobileOverlay(media.matches);
    media.addEventListener('change', sync);
    sync();
    return () => media.removeEventListener('change', sync);
  }, []);
  useLayoutEffect(() => {
    if (!mobileOverlay) return;
    const content = document.querySelector<HTMLElement>('.chat-layout-content');
    if (!content) return;
    const panel = panelRef.current;
    const wasInert = content.hasAttribute('inert');
    const root = document.documentElement;
    const body = document.body;
    const previous = {
      rootOverscrollBehavior: root.style.overscrollBehavior,
      bodyOverflow: body.style.overflow,
      contentTouchAction: content.style.touchAction,
    };

    // 문서의 실제 scrollY를 보존해야 React Router의 ScrollRestoration이
    // 앞/뒤 화면의 위치를 저장하고, 오버레이가 열린 상태에서도 새 위치로 이동할 수 있습니다.
    // body position:fixed와 top:-scrollY는 scrollY를 0으로 만들어 사용하지 않습니다.
    root.style.overscrollBehavior = 'none';
    body.style.overflow = 'hidden';
    content.style.touchAction = 'none';
    content.setAttribute('inert', '');
    if (panel && !panel.contains(document.activeElement)) {
      if (inputRef.current && !inputRef.current.closest('[hidden]')) inputRef.current.focus();
      else panel.focus();
    }
    return () => {
      if (!wasInert) content.removeAttribute('inert');
      root.style.overscrollBehavior = previous.rootOverscrollBehavior;
      body.style.overflow = previous.bodyOverflow;
      content.style.touchAction = previous.contentTouchAction;
    };
  }, [mobileOverlay]);
  useKeepFocusInPanel(panelRef, inputRef);
  const sendable = useCanSend();
  /** 패널 몸통에 무엇을 그릴지 — 메시지 · 대화 목록 · 기억 설정(F14, 대화창 안) */
  const [view, setView] = useState<PanelView>('messages');
  const toggleView = (next: PanelView) => setView(view === next ? 'messages' : next);
  const pending = useChatStore((s) => s.pending);
  const syncing = useChatStore((s) => s.syncing);
  const initialAtTop =
    session.messages.length === 0 &&
    !pending &&
    !session.sentEcho &&
    !session.progress &&
    !session.messagesLoading &&
    !syncing;
  const { scroller, onScroll, follow } = useFollowBottom(view === 'messages', initialAtTop);
  const content = draft.trim();
  const tooLong = [...content].length > MAX_MESSAGE_LENGTH;
  const failed = session.availability === 'failed';
  const blocked = failed || !sendable || session.scopeBlocked;
  const lockReason = useChatStore((s) => sendLockReason(s, session.scopeBlocked));

  // 입력 내용·대화 화면 전환 시 원래 높이를 다시 계산합니다. 세 줄 이후에는 입력창 안에서 스크롤됩니다.
  useLayoutEffect(() => {
    if (inputRef.current) resizeChatInput(inputRef.current);
  }, [draft, view]);

  // 사용자가 런처로 연 순간에만 입력칸으로 초점을 옮깁니다
  useEffect(() => {
    if (!focusRequested) return;
    inputRef.current?.focus();
    onFocused();
  }, [focusRequested, onFocused]);

  const submit = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    // 비었거나 너무 길거나 잠겨 있으면 보내지 않고 입력을 그대로 둡니다
    if (!content || tooLong || blocked) return;
    // 저장소가 보내기를 잠그는 순간(보내는 중) 입력을 비웁니다 — 보낸 글은 보내는 중 말풍선으로 보입니다.
    // 접수되지 않음이 확정되면 그 글을 입력에 되돌리는 일은 `useRestoreRejected` 가 합니다
    const off = useChatStore.subscribe((now, before) => {
      if (!before.pending && now.pending) setDraft('');
    });
    // 내가 보낸 말은 언제나 보이게 맨 아래로 갑니다
    follow();
    try {
      await session.send(content);
    } finally {
      off();
    }
  };

  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit().catch(() => undefined);
    }
  };

  return (
    <section
      data-chat-panel=""
      ref={panelRef}
      id={`${id}-panel`}
      role="dialog"
      aria-modal={mobileOverlay}
      aria-labelledby={`${id}-title`}
      // 누른 버튼이 사라졌는데 입력칸도 숨겨져 있을 때 초점을 받는 자리입니다 (useKeepFocusInPanel)
      tabIndex={-1}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          onClose();
          return;
        }
        if (event.key === 'Tab' && mobileOverlay) {
          const focusable = [
            ...event.currentTarget.querySelectorAll<HTMLElement>(
              'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
            ),
          ].filter(
            (element) =>
              !element.closest('[hidden]') &&
              element.getClientRects().length > 0 &&
              window.getComputedStyle(element).visibility !== 'hidden',
          );
          const first = focusable[0];
          const last = focusable.at(-1);
          if (!first || !last) {
            event.preventDefault();
            event.currentTarget.focus();
          } else if (
            event.shiftKey &&
            (document.activeElement === first || document.activeElement === event.currentTarget)
          ) {
            event.preventDefault();
            last.focus();
          } else if (
            !event.shiftKey &&
            (document.activeElement === last || document.activeElement === event.currentTarget)
          ) {
            event.preventDefault();
            first.focus();
          }
        }
      }}
      className="chat-side-panel fixed inset-y-0 right-0 flex w-[min(var(--chat-panel-width),100%)] flex-col overflow-y-auto overscroll-contain border-l border-line bg-white outline-none"
    >
      <ChatPanelHeader
        titleId={`${id}-title`}
        userId={userId}
        view={view}
        memoryButtonRef={memoryButtonRef}
        onNew={() => {
          session.startNew();
          setView('messages');
          inputRef.current?.focus();
        }}
        onToggleView={toggleView}
        onClose={onClose}
        onScopeChange={(next) => session.changeScope(next).catch(() => undefined)}
      />

      {/* AI 답이 글자 효과로 채워지는 요소에는 aria-live 를 두지 않습니다(글자마다 읽힐 수 있음). 완성된 문장만
          따로 둔 role="status" 영역(ChatAnswer 의 data-chat-announce)에 넣습니다(#956). 목록 전체에도 두지 않습니다 — 사용자가 보낸 말까지 다시 읽힙니다.
          min-h-16: 패널이 낮아져도 한두 줄은 보이고, 그보다 낮으면 패널이 스크롤됩니다.
          relative: 안의 sr-only(position:absolute) 글자가 이 스크롤 영역을 기준으로 자리 잡게 합니다. 없으면 기준이
          패널(section)이 되어, 대화가 길 때 그 글자가 메시지 영역 밖으로 빠져 패널 전체가 늘어나고 스크롤됩니다
          (머리줄이 밀려 올라가고 입력칸 아래가 비어 보임) */}
      <div
        ref={scroller}
        aria-label="채팅 메시지"
        onScroll={onScroll}
        className="relative flex min-h-16 flex-1 flex-col gap-4 overflow-y-auto bg-white px-4 py-5"
      >
        <PanelBody
          failed={failed}
          view={view}
          userId={userId}
          session={session}
          backToMessages={() => setView('messages')}
          leaveMemory={() => {
            // 설정 화면이 사라지기 전에 연 버튼으로 초점을 돌려 둡니다 — body 로 빠지지 않게
            memoryButtonRef.current?.focus();
            setView('messages');
          }}
        />
      </div>

      {/* 세션·범위 오류는 입력 폼 밖에 두어 목록 화면에서도 안내와 재시도가 가능합니다.
          기억 설정 화면에서는 설정 조작에 집중할 수 있게 숨깁니다. */}
      {(session.scopeFailed || session.notice) && view !== 'memory' && (
        <div
          data-chat-notices=""
          className="shrink-0 border-t border-line-strong bg-panel px-3 pb-2 pt-3"
        >
          {session.scopeFailed && (
            <div role="alert" className="mb-2 text-xs leading-5 text-coral-deep">
              <p>연습 기록의 범위를 확인하지 못했으니 다시 시도해 주세요.</p>
              <button
                type="button"
                onClick={session.retryScope}
                className="mt-1 min-h-11 border border-line-strong px-3 font-bold hover:bg-cream"
              >
                다시 시도
              </button>
            </div>
          )}
          {session.notice && (
            <div role="alert" className="mb-2 text-xs leading-5 text-coral-deep">
              <p>{noticeText(session.notice)}</p>
              {session.notice.kind === 'unconfirmed' && (
                <button
                  type="button"
                  onClick={session.retryUnconfirmed}
                  className="mt-1 min-h-11 border border-line-strong px-3 font-bold hover:bg-cream"
                >
                  {/* 접수하지 못했으면 같은 키로 다시 보내기, 결과를 모르면 같은 키로 다시 확인 — 하는 일은 같습니다 */}
                  {session.notice.notAccepted ? '다시 보내기' : '다시 확인'}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* 목록과 기억 설정에서는 입력 폼만 숨깁니다 — 쓰던 글(draft)은 그대로 남습니다. */}
      <form
        hidden={view !== 'messages'}
        onSubmit={(event) => submit(event).catch(() => undefined)}
        className={`shrink-0 bg-panel px-3 pb-3 pt-3 ${
          session.scopeFailed || session.notice ? '' : 'border-t border-line-strong'
        }`}
      >
        <label htmlFor={`${id}-input`} className="sr-only">
          채팅 메시지
        </label>
        <div
          data-chat-composer=""
          className={`flex items-end gap-2 rounded-xl border-2 bg-white p-1.5 transition-colors focus-within:ring-2 ${
            tooLong
              ? 'border-coral-deep focus-within:border-coral-deep focus-within:ring-coral-deep'
              : 'border-line-strong focus-within:border-coral focus-within:ring-coral'
          }`}
        >
          {/* 12-4 F3: maxLength로 잘라 버리면 이모지 등의 UTF-16/코드 포인트 수가 달라집니다. 초과분은 유지하고 전송만 막습니다. */}
          <textarea
            ref={inputRef}
            id={`${id}-input`}
            value={draft}
            rows={1}
            placeholder="새미나이에게 메시지 보내기…"
            aria-invalid={tooLong}
            aria-describedby={tooLong ? `${id}-limit` : undefined}
            onChange={(event) => {
              setDraft(event.target.value);
              resizeChatInput(event.currentTarget);
            }}
            onKeyDown={onComposerKeyDown}
            className="min-h-10 max-h-28 min-w-0 flex-1 resize-none overflow-y-auto border-0 bg-transparent px-2 py-2 text-sm leading-6 outline-none placeholder:text-stone focus-visible:outline-none aria-invalid:text-coral-deep"
          />
          {/* #818 · 12-4 F3: 비활성화에도 초점이 유지돼 Esc로 패널을 닫을 수 있도록 aria-disabled를 사용합니다. */}
          <button
            type="submit"
            aria-disabled={!content || tooLong || blocked}
            className="grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-coral-deep text-white hover:bg-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-coral aria-disabled:cursor-not-allowed aria-disabled:bg-cream aria-disabled:text-stone aria-disabled:hover:bg-cream"
          >
            <svg
              width="19"
              height="19"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M12 19V5M5 12l7-7 7 7" />
            </svg>
            <span className="sr-only">입력</span>
            <LockedReason reason={content && !tooLong && blocked && !failed ? lockReason : null} />
          </button>
        </div>
        {tooLong && (
          <p id={`${id}-limit`} role="alert" className="mt-1 text-xs font-bold text-coral-deep">
            {MAX_MESSAGE_LENGTH.toLocaleString('ko-KR')}자까지 보낼 수 있어요.
          </p>
        )}
        <p className="mt-2 px-1 text-[11px] text-stone">Enter 전송 · Shift+Enter 줄바꿈</p>
      </form>
    </section>
  );
}

/** 패널 몸통 — 코치를 못 불러옴 · 대화 목록 · 기억 설정 · 메시지 중 하나 */
function PanelBody({
  failed,
  view,
  userId,
  session,
  backToMessages,
  leaveMemory,
}: {
  failed: boolean;
  view: PanelView;
  userId: string;
  session: ChatSession;
  backToMessages: () => void;
  /** 기억 설정 [돌아가기] — [기억 설정] 버튼으로 초점을 돌립니다 */
  leaveMemory: () => void;
}) {
  if (failed)
    return (
      <div role="alert" className="self-start text-sm leading-6">
        <p>새미나이를 불러오지 못했으니 다시 시도해 주세요.</p>
        <button
          type="button"
          onClick={session.retryAvailability}
          className="mt-2 min-h-11 border border-line-strong px-3 text-sm font-bold hover:bg-cream"
        >
          다시 시도
        </button>
      </div>
    );
  if (view === 'memory') return <ChatMemorySettings userId={userId} onBack={leaveMemory} />;
  if (view === 'list')
    return (
      <ConversationList
        userId={userId}
        onNew={() => {
          backToMessages();
          session.startNew();
        }}
        onPick={(item) => {
          backToMessages();
          session.selectConversation(item);
        }}
      />
    );
  return (
    <>
      <MemoryIntro userId={userId} />
      <MessageList session={session} />
    </>
  );
}

/** 지난 메시지와 지금 실행의 답 — 카드는 8-3 에서 붙입니다 */
function MessageList({ session }: { session: ChatSession }) {
  const pending = useChatStore((s) => s.pending?.request.text ?? null);
  const canSendNow = useCanSend();
  // 범위를 다시 고를 때까지는 입력칸과 같이 카드 · 답 아래 버튼으로도 보내지 않습니다 (L3-07)
  const sendable = canSendNow && !session.scopeBlocked;
  const lockedReason = useChatStore((s) => sendLockReason(s, session.scopeBlocked));
  const syncing = useChatStore((s) => s.syncing);
  const actions: CardActions = {
    decide: session.decide,
    undo: session.undo,
    choose: session.choose,
    canSend: sendable,
    lockedReason: sendable ? null : lockedReason,
  };
  const shown = session.messages;
  const progress = session.progress;
  const messagesAlert = useAlertOnce(session.messagesFailed ? session.messagesError : null);
  // 접수된 내 말 — 목록에 그 실행의 내 말이 들어올 때까지 그립니다
  const echo =
    session.sentEcho &&
    !shown.some((m) => m.role === 'user' && m.run_id === session.sentEcho!.runId)
      ? session.sentEcho
      : null;
  // 실행마다 그 실행의 마지막 메시지 자리 — 끝남 안내를 그 뒤에 붙입니다
  const lastOfRun = new Map<string, number>();
  shown.forEach((m, i) => {
    if (m.run_id) lastOfRun.set(m.run_id, i);
  });
  // [이어서 확인]은 목록의 마지막 메시지가 답일 때 그 답에만
  const lastAnswer = shown.at(-1)?.role === 'assistant' ? shown.length - 1 : -1;
  /**
   * [최신 기준으로 다시 묻기] — 그 답의 질문을 새 메시지로, `again_of_message_id` 는 그 답(최신 아님 사유가 붙은 메시지).
   * 질문을 모르면(앞 페이지에 있음 등) 버튼을 두지 않습니다
   */
  const askAgainFor = (message: ChatMessage) => {
    if (message.role !== 'assistant' || !message.run_id) return undefined;
    const question = questionOf(message.run_id);
    return question
      ? () => session.send(question, null, message.message_id).catch(() => undefined)
      : undefined;
  };
  /** 그 실행을 시작한 내 말 — [다시 보내기]가 같은 질문을 새 메시지로 보냅니다 */
  const questionOf = (runId: string) =>
    shown.find((m) => m.role === 'user' && m.run_id === runId)?.text ??
    (session.sentEcho?.runId === runId ? session.sentEcho.text : null);
  // 끝난 실행의 답이 목록에 들어왔으면 목록 쪽 하나만 보입니다
  const liveInList =
    progress !== null && shown.some((m) => m.role === 'assistant' && m.run_id === progress.runId);

  // 거절 안내는 그 거절이 지금도 마지막일 때만 — 보내는 중이거나, 목록에 아직 없는 새 실행이 진행 중이면 숨깁니다
  // (다시 읽기 전 · 다시 읽기 실패에도 새 질문 위에 안내가 남지 않게)
  const askSettled = !echo && !pending && (progress === null || liveInList);
  const liveRefused = !echo && !pending && !liveInList && progress?.ended === 'REFUSED';

  const restoring = syncing || session.messagesLoading;
  return (
    <>
      {session.hasEarlier && (
        <button
          type="button"
          aria-disabled={session.loadingEarlier}
          onClick={() => {
            if (!session.loadingEarlier) session.loadEarlier();
          }}
          className="min-h-11 self-center rounded-lg border border-line-strong px-3 text-xs font-bold hover:bg-cream aria-disabled:text-stone"
        >
          이전 대화 더 보기
        </button>
      )}
      {shown.length === 0 && restoring && (
        <p className="self-start text-xs text-stone">이전 대화를 불러오고 있어요.</p>
      )}
      {shown.length === 0 && session.messagesFailed && (
        <div role={messagesAlert} className="self-start text-xs leading-5 text-coral-deep">
          {/* FE 가 정한 문구 — 문서에 없습니다 */}
          <p>대화를 불러오지 못했으니 다시 시도해 주세요.</p>
          <button
            type="button"
            onClick={session.retryMessages}
            className="mt-1 min-h-11 border border-line-strong px-3 font-bold hover:bg-cream"
          >
            다시 시도
          </button>
        </div>
      )}
      {shown.length === 0 && !pending && !echo && !restoring && !session.messagesFailed && (
        <>
          <ChatEmptyState />
          {/* 처음 열었을 때도 무엇을 물을 수 있는지 보입니다 — 거절 답 아래와 같은 안내 */}
          <AskHelp
            canSend={sendable}
            lockedReason={actions.lockedReason ?? null}
            onAsk={(text) => session.send(text).catch(() => undefined)}
          />
        </>
      )}
      {shown.map((message, i) => (
        <Fragment key={message.message_id}>
          <MessageItem
            message={message}
            actions={actions}
            onContinue={
              i === lastAnswer
                ? () => session.send(CONTINUE_TEXT).catch(() => undefined)
                : undefined
            }
            onAskAgain={askAgainFor(message)}
          />
          {message.run_id && lastOfRun.get(message.run_id) === i && (
            <EndNoticeView
              notice={session.endNotices[message.run_id]}
              // 그 뒤에 이미 다른 말이 있으면(다시 보냈음 등) 같은 질문을 또 보내지 않게 버튼을 두지 않습니다
              question={i === shown.length - 1 ? questionOf(message.run_id) : null}
              canSend={sendable}
              lockedReason={actions.lockedReason ?? null}
              onResend={(text) => session.send(text).catch(() => undefined)}
            />
          )}
          {/* 거절 답이 목록의 마지막이고 보내는 중이 아닐 때만 — 거절이 이어져도 안내가 쌓이지 않습니다 (12-4 F8 `REFUSED`) */}
          {i === lastAnswer && message.status === 'REFUSED' && askSettled && (
            <AskHelp
              canSend={sendable}
              lockedReason={actions.lockedReason ?? null}
              onAsk={(text) => session.send(text).catch(() => undefined)}
            />
          )}
        </Fragment>
      ))}
      {/* 목록에 아직 그 실행의 메시지가 없으면(다시 읽는 중 · 저장된 답이 없음) 끝에 붙입니다 */}
      {Object.entries(session.endNotices)
        .filter(([runId]) => !lastOfRun.has(runId))
        .map(([runId, notice]) => (
          <EndNoticeView
            key={runId}
            notice={notice}
            question={questionOf(runId)}
            canSend={sendable}
            lockedReason={actions.lockedReason ?? null}
            onResend={(text) => session.send(text).catch(() => undefined)}
          />
        ))}
      {echo && <p className={SELF_CLASS}>{echo.text}</p>}
      {pending && <p className={SELF_CLASS}>{pending}</p>}
      <LiveAnswer
        progress={liveInList ? null : progress}
        typing={session.typing}
        announce={session.announce}
        waiting={
          <>
            <RunWaiting onCancel={session.cancel} />
            <RunNoticeView notice={session.runNotice} onCheck={session.checkNow} />
          </>
        }
        actions={actions}
      />
      {/* 끝난 거절 답이 아직 목록에 없을 때(다시 읽는 중 · 다시 읽기 실패) — 진행 답 아래에 같은 안내를 붙입니다 */}
      {liveRefused && (
        <AskHelp
          canSend={sendable}
          lockedReason={actions.lockedReason ?? null}
          onAsk={(text) => session.send(text).catch(() => undefined)}
        />
      )}
    </>
  );
}

/** 진행 중 상태 안내 — 늦어짐 · 조회 실패에는 [상태 확인](L3-19 · L3-21) */
function RunNoticeView({ notice, onCheck }: { notice: RunNotice | null; onCheck: () => void }) {
  if (!notice) return null;
  const canCheck = notice.kind === 'slow' || (notice.kind === 'pollTrouble' && !notice.tooLarge);
  return (
    <div role="status" className="self-start text-xs leading-5 text-stone" data-run-notice="">
      <p>{runNoticeText(notice)}</p>
      {canCheck && (
        <button
          type="button"
          onClick={onCheck}
          className="mt-1 min-h-11 border border-line-strong px-3 font-bold text-ink hover:bg-cream"
        >
          상태 확인
        </button>
      )}
    </div>
  );
}

/**
 * 기다리는 동안 — "기록을 확인하고 있어요" 와 [취소] (12-4 F3). 접수 대기 · 실행 중에만 보이고,
 * 승인 대기는 확인 카드(8-3)가 대신합니다. 취소를 누르면 확정될 때까지 버튼을 잠급니다
 */
function RunWaiting({ onCancel }: { onCancel: () => void }) {
  const phase = useChatStore((s) => s.run?.phase ?? null);
  const cancelling = useChatStore((s) => s.cancelling !== null);
  if (phase !== 'QUEUED' && phase !== 'RUNNING') return null;
  return (
    <div className="flex items-center gap-3 self-start text-xs text-stone" data-run-waiting="">
      <span>{WAITING_TEXT}</span>
      {/* `disabled` 로 두면 초점이 body 로 빠집니다 (#818) — aria-disabled 로 두고 누름을 무시합니다 */}
      <button
        type="button"
        aria-disabled={cancelling}
        onClick={() => {
          if (!cancelling) onCancel();
        }}
        className="min-h-11 border border-line-strong px-3 font-bold text-ink hover:bg-cream aria-disabled:cursor-not-allowed aria-disabled:text-stone"
      >
        {cancelling ? '취소 중' : '취소'}
      </button>
    </div>
  );
}

const SELF_CLASS =
  'max-w-[88%] self-end whitespace-pre-wrap break-words rounded-xl bg-cream px-4 py-3 text-sm leading-6';

function MessageItem({
  message,
  actions,
  onContinue,
  onAskAgain,
}: {
  message: ChatMessage;
  actions: CardActions;
  onContinue?: () => void;
  onAskAgain?: () => void;
}) {
  if (message.role === 'user') return <p className={SELF_CLASS}>{message.text}</p>;
  if (message.role === 'system' || message.masked)
    return <p className={AGENT_BUBBLE}>{message.text}</p>;
  return (
    <StoredAnswer
      message={message}
      actions={actions}
      onContinue={onContinue}
      onAskAgain={onAskAgain}
    />
  );
}

/** 이미 알린 안내 — 대화 목록을 열었다 닫거나 자리를 옮겨 다시 그려져도 또 읽지 않습니다 (12-4 "오류는 한 번 알린다") */
const announced = new WeakSet<object>();

/**
 * 처음 그리는 안내에만 `role="alert"` 를 줍니다. `token` 은 그 안내 하나를 가리키는 값(안내 객체 · 오류 객체)입니다.
 * 같은 자리에서 그 안내를 계속 그리는 동안에는 role 을 바꾸지 않습니다
 */
function useAlertOnce(token: object | null): 'alert' | undefined {
  const fresh = (t: object | null) => ({ token: t, alert: t !== null && !announced.has(t) });
  // 이 자리에서 그 안내를 처음 그릴 때 정해 둡니다 — 안내가 바뀌면 그릴 때 다시 정합니다
  const [seen, setSeen] = useState(() => fresh(token));
  let now = seen;
  if (seen.token !== token) {
    now = fresh(token);
    setSeen(now);
  }
  useEffect(() => {
    if (token) announced.add(token);
  }, [token]);
  return now.alert ? 'alert' : undefined;
}

/** 실행이 끝나며 붙는 안내 — 오류는 한 번 알립니다(role="alert"). [다시 보내기]는 같은 질문을 새 메시지로 */
function EndNoticeView({
  notice,
  question,
  canSend,
  lockedReason,
  onResend,
}: {
  notice: EndNotice | undefined;
  question: string | null;
  canSend: boolean;
  lockedReason: string | null;
  onResend: (text: string) => void;
}) {
  const role = useAlertOnce(notice ?? null);
  if (!notice) return null;
  return (
    <div role={role} className="self-start text-xs leading-5 text-coral-deep" data-end-notice="">
      <p>{notice.text}</p>
      {notice.resend && question && (
        <button
          type="button"
          aria-disabled={!canSend}
          onClick={() => {
            if (canSend) onResend(question);
          }}
          className="mt-1 min-h-11 border border-line-strong px-3 font-bold text-ink hover:bg-cream aria-disabled:cursor-not-allowed aria-disabled:text-stone"
        >
          다시 보내기
          <LockedReason reason={canSend ? null : lockedReason} />
        </button>
      )}
    </div>
  );
}
