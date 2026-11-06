import {
  Component,
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
import { canShowChat, chatLauncherBottom } from './chatRoutes';

/**
 * Frontend-only preview. No message is sent to a server and no AI reply is
 * fabricated. This host intentionally lives above the page routes so the
 * panel and its temporary messages survive navigation.
 * 채팅에서 오류가 나면 채팅만 사라지고 화면은 그대로 남습니다.
 */
export function ChatAgentHost() {
  // 연동이 끝나 빌드 스위치를 켜기 전까지는 배포에 섞여도 아무것도 그리지 않습니다 (12-4 F1 "사용 가능 여부").
  // #951 의 isChatAgentBuildEnabled() 와 같은 식입니다 — #951 이 합쳐지면 그 함수로 바꿉니다
  if (import.meta.env.VITE_CHAT_AGENT !== 'true') return null;

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

/**
 * 입력과 메시지는 여기 두어 채팅이 숨는 화면을 지나도 남습니다.
 * 같은 탭에서 계정이 바뀌는 길(로그아웃 · 탈퇴 · Google 로그인)은 모두 페이지를 새로 열어 이 상태도 함께 사라집니다.
 * 다른 탭에서 계정을 바꾸면 이 탭의 입력 · 메시지는 남습니다 — 서버로 가지 않는 로컬 글이라 두고,
 * 연동할 때 사용자가 바뀌면 비우는 저장소(#952 chatStore)로 옮깁니다.
 */
function ChatLayer() {
  const { pathname } = useLocation();
  const allowed = canShowChat(pathname);
  const launcherBottom = chatLauncherBottom(pathname);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [messages, setMessages] = useState<string[]>([]);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const id = useId();
  const content = draft.trim();
  const tooLong = [...content].length > MAX_MESSAGE_LENGTH;

  // 숨는 화면으로 가면 닫습니다. 돌아왔을 때 패널이 다시 열려 초점을 빼앗지 않게 합니다
  if (!allowed && open) setOpen(false);

  // 패널은 런처로만 열리므로, 열린 순간이 곧 사용자가 연 순간입니다
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const close = () => {
    setOpen(false);
    launcherRef.current?.focus();
  };

  const submit = (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    // 비었거나 너무 길면 보내지 않고 입력을 그대로 둡니다
    if (!content || tooLong) return;
    setMessages((current) => [...current, content]);
    setDraft('');
  };

  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  if (!allowed) return null;

  return (
    <SignedInOnly>
      {/* 런처는 오른쪽 아래의 44px 마스코트 아이콘입니다. 허용 화면은 본문 오른쪽 여백이 40px 뿐이라 오른쪽에 붙은
          컨트롤과 자리를 나눠 씁니다. 화면마다의 아래 여백과 그 이유는 chatLauncherBottom 에 있습니다 */}
      <div
        // data-chat-launcher: 장치 점검이 런처 자리만큼 오른쪽 여백을 넓히는 표시입니다 (index.css)
        data-chat-launcher=""
        style={{ '--chat-launcher-bottom': `${launcherBottom}px` } as CSSProperties}
        className="light-surface fixed bottom-[var(--chat-launcher-bottom)] right-2 z-[70] text-ink"
      >
        {/* 패널은 런처 위 오른쪽에 열리고 상단바(--spacing-app-header) 아래에서 멈춥니다.
            4.25rem = 런처 높이 2.75rem + 위아래 간격 0.75rem 둘.
            낮은 창에서는 메시지 목록이 먼저 줄고, 그보다 낮으면(150% · 200% 확대) 패널 전체가 스크롤되어
            머리 · 입력칸 · 버튼이 잘리지 않습니다 (12-4 "키보드 · 접근성") */}
        {open && (
          <section
            id={`${id}-panel`}
            role="dialog"
            aria-modal="false"
            aria-labelledby={`${id}-title`}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.stopPropagation();
                close();
              }
            }}
            className="absolute bottom-full right-0 mb-3 flex h-[36rem] max-h-[calc(100dvh-var(--spacing-app-header)-var(--chat-launcher-bottom)-4.25rem)] w-[min(26rem,calc(100vw-2rem))] flex-col overflow-y-auto rounded-2xl border-2 border-ink bg-panel shadow-[5px_5px_0_var(--color-ink)]"
          >
            <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line-strong px-5 py-3">
              <div>
                <h2 id={`${id}-title`} className="text-base font-bold">
                  채팅 에이전트
                </h2>
                <p className="mt-1 text-xs text-stone">AI 연결 전 미리보기</p>
              </div>
              <button
                type="button"
                onClick={close}
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
              <p className="self-start rounded-xl border border-line-strong bg-white px-4 py-3 text-sm leading-6">
                발표 기록과 연습에 대해 질문하는 기능을 준비하고 있어요.
              </p>
              {messages.map((message, index) => (
                <p
                  key={index}
                  className="max-w-[88%] self-end whitespace-pre-wrap break-words rounded-xl bg-cream px-4 py-3 text-sm leading-6"
                >
                  {message}
                </p>
              ))}
            </div>

            <form onSubmit={submit} className="shrink-0 border-t border-line-strong p-3">
              <p className="mb-2 text-xs leading-5 text-stone">
                메시지는 서버로 보내지 않고 새로고침하면 사라져요.
              </p>
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
                <p
                  id={`${id}-limit`}
                  role="alert"
                  className="mt-1 text-xs font-bold text-coral-deep"
                >
                  {MAX_MESSAGE_LENGTH.toLocaleString('ko-KR')}자까지 보낼 수 있어요.
                </p>
              )}
              <div className="mt-2 flex items-center justify-between gap-3">
                <span className="text-xs text-stone">Enter 전송 · Shift+Enter 줄바꿈</span>
                {/* `disabled` 로 두면 누른 직후 초점이 body 로 빠져 Esc 로 닫히지 않습니다 (#818).
                    그래서 `aria-disabled` 로 두고, 빈 입력 · 너무 긴 입력은 submit 이 무시합니다 */}
                <button
                  type="submit"
                  aria-disabled={!content || tooLong}
                  className="min-h-11 shrink-0 rounded-lg border-2 border-ink bg-coral-deep px-5 text-sm font-bold text-white hover:bg-ink aria-disabled:cursor-not-allowed aria-disabled:border-line-strong aria-disabled:bg-cream aria-disabled:text-stone aria-disabled:hover:bg-cream"
                >
                  입력
                </button>
              </div>
            </form>
          </section>
        )}

        <button
          ref={launcherRef}
          type="button"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? `${id}-panel` : undefined}
          onClick={() => (open ? close() : setOpen(true))}
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
    </SignedInOnly>
  );
}

/**
 * 채팅을 띄우는 화면에서만 세션을 구독합니다. 재조회가 한 번 실패해도
 * 받아 둔 로그인 정보가 있으면 그대로 둡니다 — 열린 패널이 사라지지 않게 합니다
 */
function SignedInOnly({ children }: { children: ReactNode }) {
  const session = useSession();
  return session.data?.id ? children : null;
}
