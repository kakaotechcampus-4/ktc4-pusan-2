import {
  Component,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { useLocation } from 'react-router';
import { useSession } from '@/shared/api/useSession';
import { isChatAgentBuildEnabled } from '@/shared/lib/chatAgent';
import type { ChatMessage } from '@/types/chat';
import { AGENT_BUBBLE, LiveAnswer, StoredAnswer, WAITING_TEXT } from './ChatAnswer';
import type { CardActions } from './ChatCards';
import { ANSWER_FAILED_TEXT } from './chatResponse';
import { chatLauncherBottom, chatRouteOf, type ChatRouteInfo } from './chatRoutes';
import { canSend, useChatStore, type ScreenScope } from './chatStore';
import { useChatSession, type ChatNotice, type ChatSession } from './useChatSession';

/**
 * 대화형 코치 에이전트의 버튼 · 팝업 (`docs/ai/features/chat-agent.md` 12-4 F1). 라우터 최상위 레이아웃에 있어
 * 화면을 옮겨도 대화가 이어지고, 대화 상태는 저장소(`chatStore`)에 둡니다.
 * 채팅에서 오류가 나면 채팅만 사라지고 화면은 그대로 남습니다.
 */
export function ChatAgentHost() {
  // 연동이 끝나 빌드 스위치를 켜기 전까지는 배포에 섞여도 아무것도 그리지 않습니다 (12-4 F1 "사용 가능 여부")
  if (!isChatAgentBuildEnabled()) return null;

  return (
    <ChatErrorBoundary>
      <ChatLayer />
    </ChatErrorBoundary>
  );
}

class ChatErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/** 메시지 글자 수 상한. 서버와 같은 기준(Python `len`, 코드 포인트 수)으로 셉니다 (12-4 F3) */
const MAX_MESSAGE_LENGTH = 2000;

function ChatLayer() {
  const { pathname, search } = useLocation();
  const info = chatRouteOf(pathname, search);
  const hidden = info === null;
  // 입력은 여기 두어 패널을 닫거나 채팅이 숨는 화면을 지나도 남습니다. 사용자가 바뀌면 비웁니다
  const [draft, setDraft] = useState({ userId: null as string | null, text: '' });

  // 숨는 화면으로 가면 닫습니다. 돌아왔을 때 패널이 다시 열려 초점을 빼앗지 않게 합니다
  useEffect(() => {
    if (hidden && useChatStore.getState().open) useChatStore.getState().closeChat();
  }, [hidden]);

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
  const id = session.data?.id ?? null;
  const signedOut = session.data === null;
  const storeUser = useChatStore((s) => s.userId);

  useEffect(() => {
    if (id) useChatStore.getState().setUser(id);
    else if (signedOut) useChatStore.getState().setUser(null);
  }, [id, signedOut]);

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

  // 조회 중 · 꺼짐이면 숨깁니다. 실패가 잇달면 버튼은 보이고 열면 다시 시도를 보입니다 (12-4 F1 "사용 가능 여부")
  if (session.availability === 'checking' || session.availability === 'off') return null;

  const close = () => {
    useChatStore.getState().closeChat();
    launcherRef.current?.focus();
  };

  return (
    // 런처는 오른쪽 아래의 44px 마스코트 아이콘입니다. 허용 화면은 본문 오른쪽 여백이 40px 뿐이라 오른쪽에 붙은
    // 컨트롤과 자리를 나눠 씁니다. 화면마다의 아래 여백과 그 이유는 chatLauncherBottom 에 있습니다
    <div
      // data-chat-launcher: 장치 점검이 런처 자리만큼 오른쪽 여백을 넓히는 표시입니다 (index.css)
      data-chat-launcher=""
      style={{ '--chat-launcher-bottom': `${launcherBottom}px` } as CSSProperties}
      className="light-surface fixed bottom-[var(--chat-launcher-bottom)] right-2 z-[70] text-ink"
    >
      {open && (
        <ChatPanel
          id={id}
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
        aria-label="채팅 에이전트"
        title="채팅 에이전트"
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

/** 칩 자리의 범위 이름 — 칩 · 피치 고르기는 8-4 에서 붙입니다 */
function scopeLabel(scope: ScreenScope): string {
  if (scope === null) return '범위 확인 중';
  if (scope.pitchId === null) return '전체';
  return scope.title ? `피치: ${scope.title}` : '피치';
}

function noticeText(notice: ChatNotice): string {
  switch (notice.kind) {
    case 'unconfirmed':
      return '전송 여부를 확인하지 못했으니 다시 확인해 주세요.';
    case 'criteriaTooLong':
      return '평가 기준이 2,000자를 넘어 보낼 수 없어요.';
    case 'sessionExpired':
      return '로그인이 만료됐으니 다시 로그인해 주세요.';
    case 'sendFailed':
      // 코드별 문구(F8)는 8-5 에서 채웁니다
      return notice.code === 'AGENT_BUSY'
        ? '오늘은 사용량이 많아 잠시 뒤 다시 이용해 주세요.'
        : ANSWER_FAILED_TEXT;
  }
}

function ChatPanel({
  id,
  session,
  onClose,
  focusRequested,
  onFocused,
  draft,
  setDraft,
}: {
  id: string;
  session: ChatSession;
  onClose: () => void;
  /** 런처로 연 때만 true — 그때만 입력칸에 초점을 주고 `onFocused` 로 알립니다 */
  focusRequested: boolean;
  onFocused: () => void;
} & DraftProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const latestDraft = useRef(draft);
  useEffect(() => {
    latestDraft.current = draft;
  });
  const scope = useChatStore((s) => s.scope);
  const sendable = useChatStore((s) => canSend(s));
  const content = draft.trim();
  const tooLong = [...content].length > MAX_MESSAGE_LENGTH;
  const failed = session.availability === 'failed';
  const blocked = failed || !sendable;

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
    // 접수되지 않음이 확정되면 그 글을 입력에 되돌립니다(그사이 새로 쓴 글이 있으면 두고)
    const off = useChatStore.subscribe((now, before) => {
      if (!before.pending && now.pending) setDraft('');
    });
    let sent = false;
    try {
      sent = await session.send(content);
    } finally {
      off();
    }
    if (!sent && useChatStore.getState().pending === null && latestDraft.current.trim() === '')
      setDraft(content);
  };

  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  };

  return (
    // 패널은 런처 위 오른쪽에 열리고 상단바(--spacing-app-header) 아래에서 멈춥니다.
    // 4.25rem = 런처 높이 2.75rem + 위아래 간격 0.75rem 둘.
    // 낮은 창에서는 메시지 목록이 먼저 줄고, 그보다 낮으면(150% · 200% 확대) 패널 전체가 스크롤되어
    // 머리 · 입력칸 · 버튼이 잘리지 않습니다 (12-4 "키보드 · 접근성")
    <section
      id={`${id}-panel`}
      role="dialog"
      aria-modal="false"
      aria-labelledby={`${id}-title`}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          onClose();
        }
      }}
      className="absolute bottom-full right-0 mb-3 flex h-[36rem] max-h-[calc(100dvh-var(--spacing-app-header)-var(--chat-launcher-bottom)-4.25rem)] w-[min(26rem,calc(100vw-2rem))] flex-col overflow-y-auto rounded-2xl border-2 border-ink bg-panel shadow-[5px_5px_0_var(--color-ink)]"
    >
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line-strong px-5 py-3">
        <div>
          <h2 id={`${id}-title`} className="text-base font-bold">
            채팅 에이전트
          </h2>
          <p className="mt-1 text-xs text-stone" data-chat-scope="">
            {scopeLabel(scope)}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="채팅 에이전트 닫기"
          className="min-h-11 min-w-11 border border-line-strong px-3 text-sm font-bold hover:bg-cream"
        >
          닫기
        </button>
      </header>

      {/* AI 답을 붙일 때는 그 답 요소에만 aria-live 를 둡니다. 목록 전체에 두면 사용자가 보낸 말까지 다시 읽힙니다.
          min-h-16: 패널이 낮아져도 한두 줄은 보이고, 그보다 낮으면 패널이 스크롤됩니다 */}
      <div
        aria-label="채팅 메시지"
        className="flex min-h-16 flex-1 flex-col gap-4 overflow-y-auto px-5 py-5"
      >
        {failed ? (
          <div role="alert" className="self-start text-sm leading-6">
            <p>코치를 불러오지 못했으니 다시 시도해 주세요.</p>
            <button
              type="button"
              onClick={session.retryAvailability}
              className="mt-2 min-h-11 border border-line-strong px-3 text-sm font-bold hover:bg-cream"
            >
              다시 시도
            </button>
          </div>
        ) : (
          <MessageList session={session} />
        )}
      </div>

      <form
        onSubmit={(event) => void submit(event)}
        className="shrink-0 border-t border-line-strong p-3"
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
                다시 확인
              </button>
            )}
          </div>
        )}
        <label htmlFor={`${id}-input`} className="sr-only">
          채팅 메시지
        </label>
        {/* maxLength 는 UTF-16 으로 세고(이모지 하나가 2자) 넘친 글을 말없이 잘라서 쓰지 않습니다.
            넘으면 입력을 그대로 두고 보내기만 막습니다 (12-4 F3) */}
        <textarea
          ref={inputRef}
          id={`${id}-input`}
          value={draft}
          rows={2}
          placeholder="메시지를 입력하세요"
          aria-invalid={tooLong}
          aria-describedby={tooLong ? `${id}-limit` : undefined}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onComposerKeyDown}
          className="w-full resize-none rounded-lg border-2 border-line-strong bg-white p-3 text-sm outline-offset-2 focus-visible:outline-2 focus-visible:outline-coral aria-invalid:border-coral-deep"
        />
        {tooLong && (
          <p id={`${id}-limit`} role="alert" className="mt-1 text-xs font-bold text-coral-deep">
            {MAX_MESSAGE_LENGTH.toLocaleString('ko-KR')}자까지 보낼 수 있어요.
          </p>
        )}
        <div className="mt-2 flex items-center justify-between gap-3">
          <span className="text-xs text-stone">Enter 전송 · Shift+Enter 줄바꿈</span>
          {/* `disabled` 로 두면 누른 직후 초점이 body 로 빠져 Esc 로 닫히지 않습니다 (#818).
              그래서 `aria-disabled` 로 두고, 빈 입력 · 너무 긴 입력 · 잠금은 submit 이 무시합니다 */}
          <button
            type="submit"
            aria-disabled={!content || tooLong || blocked}
            className="min-h-11 shrink-0 rounded-lg border-2 border-ink bg-coral-deep px-5 text-sm font-bold text-white hover:bg-ink aria-disabled:cursor-not-allowed aria-disabled:border-line-strong aria-disabled:bg-cream aria-disabled:text-stone aria-disabled:hover:bg-cream"
          >
            입력
          </button>
        </div>
      </form>
    </section>
  );
}

/** 지난 메시지와 지금 실행의 답 — 카드는 8-3 에서 붙입니다 */
function MessageList({ session }: { session: ChatSession }) {
  const pending = useChatStore((s) => s.pending?.request.text ?? null);
  const sendable = useChatStore((s) => canSend(s));
  const actions: CardActions = {
    decide: session.decide,
    undo: session.undo,
    choose: session.choose,
    canSend: sendable,
  };
  const shown = session.messages;
  const progress = session.progress;
  // 접수된 내 말 — 목록에 그 실행의 내 말이 들어올 때까지 그립니다
  const echo =
    session.sentEcho &&
    !shown.some((m) => m.role === 'user' && m.run_id === session.sentEcho!.runId)
      ? session.sentEcho
      : null;
  // 끝난 실행의 답이 목록에 들어왔으면 목록 쪽 하나만 보입니다
  const liveInList =
    progress !== null && shown.some((m) => m.role === 'assistant' && m.run_id === progress.runId);

  return (
    <>
      {shown.length === 0 &&
        !pending &&
        !echo &&
        !session.messagesLoading &&
        !session.messagesFailed && (
          <p className={AGENT_BUBBLE}>발표 기록과 연습에 대해 물어보세요.</p>
        )}
      {shown.map((message) => (
        <MessageItem key={message.message_id} message={message} actions={actions} />
      ))}
      {echo && <p className={SELF_CLASS}>{echo.text}</p>}
      {pending && <p className={SELF_CLASS}>{pending}</p>}
      <LiveAnswer
        progress={liveInList ? null : progress}
        typing={session.typing}
        announce={session.announce}
        waiting={<RunWaiting onCancel={session.cancel} />}
        actions={actions}
      />
    </>
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

function MessageItem({ message, actions }: { message: ChatMessage; actions: CardActions }) {
  if (message.role === 'user') return <p className={SELF_CLASS}>{message.text}</p>;
  if (message.role === 'system' || message.masked)
    return <p className={AGENT_BUBBLE}>{message.text}</p>;
  return <StoredAnswer message={message} actions={actions} />;
}
