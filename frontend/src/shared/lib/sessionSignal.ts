/**
 * "로그아웃했다" 신호 — 이 탭과 다른 탭 모두에.
 *
 * 로그인은 쿠키를 탭끼리 나눠 쓰지만, 한 탭에서 로그아웃해도 다른 탭의 메모리 상태와 sessionStorage 사본은
 * 그대로 남습니다. 대화 에이전트는 다른 탭의 로그아웃도 반영해 앞 사람의 대화를 비워야 합니다
 * (`docs/ai/features/chat-agent.md` 12-4 F1 "다른 탭의 로그아웃도 반영한다").
 *
 * 범위: 신호를 주고받기만 합니다. 다른 탭의 로그인 화면 전환까지 바꾸지는 않습니다 — 그건 앱 전체의
 * 로그인 흐름이라 따로 정합니다. `BroadcastChannel` 이 없는 브라우저에서는 이 탭에만 알립니다.
 */
const CHANNEL = 'pitchcoach-session';
const SIGNED_OUT = 'signed-out';
/**
 * 이 탭의 표시. 채널 메시지는 보낸 객체만 빼고 **같은 탭의 다른 채널 객체에도** 갑니다 —
 * 이 탭의 듣는 쪽은 이미 바로 불렀으니, 자기 탭에서 온 메시지는 버립니다
 */
const TAB = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

interface Signal {
  type: typeof SIGNED_OUT;
  tab: string;
}

const isSignal = (v: unknown): v is Signal =>
  typeof v === 'object' &&
  v !== null &&
  (v as Signal).type === SIGNED_OUT &&
  typeof (v as Signal).tab === 'string';

const local = new Set<() => void>();

function channel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === 'undefined') return null;
  try {
    return new BroadcastChannel(CHANNEL);
  } catch {
    return null;
  }
}

/**
 * 로그아웃했음을 알립니다 — 이 탭의 듣는 쪽은 바로(새로고침 전에), 다른 탭은 채널로.
 * 듣는 쪽 하나가 실패해도 나머지 · 다른 탭 알림 · 부른 쪽의 새로고침은 그대로 갑니다
 */
export function announceSignedOut() {
  for (const listener of local) {
    try {
      listener();
    } catch (error) {
      console.error('로그아웃 신호를 처리하지 못했어요', error);
    }
  }
  const ch = channel();
  if (!ch) return;
  ch.postMessage({ type: SIGNED_OUT, tab: TAB } satisfies Signal);
  ch.close();
}

/** 이 탭이나 다른 탭의 로그아웃을 듣습니다. 돌려준 함수로 그만 듣습니다 */
export function onSignedOut(listener: () => void): () => void {
  local.add(listener);
  const ch = channel();
  if (ch)
    ch.onmessage = (event: MessageEvent) => {
      if (isSignal(event.data) && event.data.tab !== TAB) listener();
    };
  return () => {
    local.delete(listener);
    ch?.close();
  };
}
