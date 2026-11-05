import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { useLocation } from 'react-router';
import { useSession } from '@/shared/api/useSession';
import { canShowChat } from './chatRoutes';

type LocalMessage = { id: number; content: string };
type LocalHistory = { userId: string; messages: LocalMessage[] };

/**
 * Frontend-only preview. No message is sent to a server and no AI reply is
 * fabricated. This host intentionally lives above the page routes so the
 * panel and its temporary messages survive navigation.
 */
export function ChatAgentHost() {
  const { pathname } = useLocation();
  const session = useSession();
  const userId = session.data?.id ?? null;
  const visible = canShowChat(pathname) && !session.isPending && !session.isError && !!userId;

  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [history, setHistory] = useState<LocalHistory>({ userId: '', messages: [] });
  const launcherRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const previousUserRef = useRef<string | null>(null);
  const nextMessageId = useRef(0);
  const titleId = useId();
  const panelId = useId();

  // Never expose one account's temporary messages to another account.
  useEffect(() => {
    if (session.isPending || session.isError) return;
    if (previousUserRef.current !== userId) {
      if (previousUserRef.current !== null) {
        setOpen(false);
        setDraft('');
        setHistory({ userId: '', messages: [] });
      }
      previousUserRef.current = userId;
    }
  }, [session.isPending, session.isError, userId]);

  useEffect(() => {
    if (visible && open) inputRef.current?.focus();
  }, [visible, open]);

  const close = () => {
    setOpen(false);
    requestAnimationFrame(() => launcherRef.current?.focus());
  };

  const submit = (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    if (!userId) return;
    const content = draft.trim();
    if (!content || content.length > 2000) return;
    setHistory((current) => ({
      userId,
      messages: [
        ...(current.userId === userId ? current.messages : []),
        { id: ++nextMessageId.current, content },
      ],
    }));
    setDraft('');
  };

  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  if (!visible) return null;
  const messages = history.userId === userId ? history.messages : [];

  return (
    <div className="pitch-create fixed bottom-4 right-4 z-[70] text-ink">
      {open && (
        <section
          id={panelId}
          role="dialog"
          aria-modal="false"
          aria-labelledby={titleId}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.stopPropagation();
              close();
            }
          }}
          className="absolute bottom-20 right-0 flex h-[36rem] max-h-[calc(100dvh-7rem)] w-[min(26rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border-2 border-ink bg-panel shadow-[5px_5px_0_var(--color-ink)]"
        >
          <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line-strong px-5 py-4">
            <div>
              <h2 id={titleId} className="text-base font-bold">
                채팅 에이전트
              </h2>
              <p className="mt-1 text-xs text-stone">화면 미리보기 · AI 연결 전</p>
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

          <div
            aria-label="채팅 메시지"
            aria-live="polite"
            className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-5"
          >
            <p className="self-start rounded-xl border border-line-strong bg-white px-4 py-3 text-sm leading-6">
              발표 기록과 연습에 대해 질문하는 기능을 준비하고 있어요.
            </p>
            {messages.map((message) => (
              <p
                key={message.id}
                className="max-w-[88%] self-end whitespace-pre-wrap break-words rounded-xl bg-cream px-4 py-3 text-sm leading-6"
              >
                {message.content}
              </p>
            ))}
          </div>

          <form onSubmit={submit} className="shrink-0 border-t border-line-strong p-4">
            <p className="mb-3 text-xs leading-5 text-stone">
              현재는 UI 시연입니다. 입력한 메시지는 서버에 전송되지 않으며 새로고침하면 사라집니다.
            </p>
            <label htmlFor={panelId + '-input'} className="sr-only">
              채팅 메시지
            </label>
            <textarea
              ref={inputRef}
              id={panelId + '-input'}
              value={draft}
              maxLength={2000}
              rows={2}
              placeholder="메시지를 입력하세요"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onComposerKeyDown}
              className="w-full resize-none rounded-lg border-2 border-line-strong bg-white p-3 text-sm outline-offset-2 focus-visible:outline-2 focus-visible:outline-coral"
            />
            <div className="mt-2 flex items-center justify-between gap-3">
              <span className="text-xs text-stone">Enter 전송 · Shift+Enter 줄바꿈</span>
              <button
                type="submit"
                disabled={!draft.trim()}
                className="min-h-11 shrink-0 rounded-lg border-2 border-ink bg-coral-deep px-5 text-sm font-bold text-white hover:bg-ink disabled:cursor-not-allowed disabled:border-line-strong disabled:bg-cream disabled:text-stone"
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
        aria-controls={panelId}
        onClick={() => (open ? close() : setOpen(true))}
        className="ml-auto flex min-h-16 min-w-16 items-center justify-center rounded-2xl border-2 border-ink bg-coral-deep px-3 py-2 text-center text-xs font-bold leading-4 text-white shadow-[3px_3px_0_var(--color-ink)] hover:bg-ink focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-coral"
      >
        채팅
        <br />
        에이전트
      </button>
    </div>
  );
}
