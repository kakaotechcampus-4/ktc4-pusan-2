import {
  Component,
  createRef,
  useEffect,
  useState,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from 'react';
import { useLocation } from 'react-router';
import { CHAT_LAUNCHER_CLASS, canShowChat, chatLauncherBottom } from '../rules/chatRoutes';

/** 대화창이 렌더 중에 던졌을 때 보이는 문구 (`docs/ai/features/chat-agent.md` L3-36 · 7-9) */
export const CHAT_FAILED_TEXT = '대화창에 문제가 생겼으니 다시 열어 주세요.';

interface Props {
  children: ReactNode;
  /**
   * [다시 시도]를 누른 직후, 대화창을 다시 그리기 전에 부릅니다. `failures` 는 이 페이지에서 대화창이 실패한 횟수입니다.
   * 대화 상태가 React 바깥 저장소에 있으면 여기서 정리합니다 — 다시 그려도 같은 상태 때문에 또 던지지 않게 합니다
   */
  onReset?: (failures: number) => void;
}

interface State {
  failed: boolean;
  /** [다시 시도]를 누른 횟수. 안내의 key 라서, 다시 시도한 뒤 또 실패하면 안내를 새로 그려 다시 알립니다 */
  retries: number;
}

/**
 * 대화 영역의 오류 경계 (12-4 F8 · L3-36). 대화창이 렌더 중에 던지면 대화만 닫고 런처 자리에 안내와 [다시 시도]를
 * 둡니다 — 리포트 · 연습 시작 · 장치 점검 같은 바깥 페이지는 이 경계 밖이라 그대로 씁니다.
 * 다시 그리는 것은 [다시 시도]를 누를 때뿐입니다. 같은 오류가 되풀이돼도 저절로 다시 그리지 않아 무한히 돌지 않습니다.
 */
export class ChatErrorBoundary extends Component<Props, State> {
  state: State = { failed: false, retries: 0 };
  private retryRef = createRef<HTMLButtonElement>();

  static getDerivedStateFromError(): Partial<State> {
    return { failed: true };
  }

  private retry = () => {
    this.props.onReset?.(this.state.retries + 1);
    this.setState((s) => ({ failed: false, retries: s.retries + 1 }));
  };

  /** 실패 · 다시 시도로 대화창과 안내가 바뀌기 직전의 초점 — 바뀌며 사라진 요소였는지 commit 뒤에 봅니다 */
  getSnapshotBeforeUpdate(_: Props, prev: State): Element | null {
    const changed = prev.failed !== this.state.failed || prev.retries !== this.state.retries;
    return changed ? document.activeElement : null;
  }

  /**
   * 초점이 있던 요소가 사라졌으면 body 로 빠지지 않게 옮깁니다 (#818).
   * 실패했으면 [다시 시도]로, 다시 그리는 데 성공했으면 런처로 갑니다. 바깥 페이지에 초점이 있었으면 건드리지 않습니다.
   * Safari 는 버튼을 눌러도 초점을 주지 않아, 다시 시도한 때는 body 였어도 옮깁니다
   */
  componentDidUpdate(_: Props, prev: State, before: Element | null) {
    if (!before) return;
    const retried = prev.retries !== this.state.retries;
    if (before.isConnected && !(retried && before === document.body)) return;
    if (this.state.failed) this.retryRef.current?.focus();
    else
      document
        .querySelector<HTMLElement>('[data-chat-launcher] button[aria-haspopup="dialog"]')
        ?.focus();
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <ChatFailed key={this.state.retries} retryRef={this.retryRef} onRetry={this.retry} />;
  }
}

/**
 * 런처 자리의 안내. 채팅이 숨는 화면(연습 무대 등)에서는 그리지 않습니다.
 * [닫기]는 안내를 런처 크기의 [다시 시도] 버튼으로 접습니다 — 안내도 왼쪽 아래에 표시합니다.
 * 정상 런처와 같은 `data-chat-launcher` 를 둡니다.
 * index.css의 화면 여백 보정과 오류 경계의 초점 복귀가 이 표시를 사용합니다.
 */
function ChatFailed({
  retryRef,
  onRetry,
}: {
  retryRef: RefObject<HTMLButtonElement>;
  onRetry: () => void;
}) {
  const { pathname } = useLocation();
  const [folded, setFolded] = useState(false);

  // 접으면 누른 [닫기]가 사라지므로 접힌 [다시 시도]로 초점을 옮깁니다 (#818)
  useEffect(() => {
    if (folded) retryRef.current?.focus();
  }, [folded, retryRef]);

  if (!canShowChat(pathname)) return null;
  const defaultBottom = chatLauncherBottom(pathname);

  return (
    <div
      data-chat-launcher=""
      style={{ '--chat-launcher-default-bottom': `${defaultBottom}px` } as CSSProperties}
      className={CHAT_LAUNCHER_CLASS}
    >
      {folded ? (
        <button
          ref={retryRef}
          type="button"
          onClick={onRetry}
          aria-label="대화창 다시 시도"
          title={CHAT_FAILED_TEXT}
          className="relative block h-11 w-11 overflow-hidden rounded-full border-2 border-ink bg-coral-deep shadow-[3px_3px_0_var(--color-ink)] hover:bg-ink focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-coral"
        >
          <img
            src="/icon-192.png"
            alt=""
            className="absolute left-[1.2px] top-[-3.8px] h-11 w-11 max-w-none"
          />
        </button>
      ) : (
        <section
          aria-label="대화창 오류"
          className="w-[min(18rem,calc(100vw-2rem))] rounded-2xl border-2 border-ink bg-panel p-4 shadow-[3px_3px_0_var(--color-ink)]"
        >
          {/* 안내는 실패마다 한 번 읽힙니다. 버튼에는 이 문장을 잇지 않아 초점이 옮겨 가도 두 번 읽히지 않습니다 */}
          <p role="alert" className="text-sm leading-6">
            {CHAT_FAILED_TEXT}
          </p>
          <div className="mt-3 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setFolded(true)}
              className="min-h-11 border border-line-strong px-3 text-sm font-bold hover:bg-cream"
            >
              닫기
            </button>
            <button
              ref={retryRef}
              type="button"
              onClick={onRetry}
              className="min-h-11 rounded-lg border-2 border-ink bg-coral-deep px-4 text-sm font-bold text-white hover:bg-ink"
            >
              다시 시도
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
