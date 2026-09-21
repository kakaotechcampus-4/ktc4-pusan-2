import { http, HttpResponse } from 'msw';

// Default mocks remain unauthenticated; Google navigation is never faked.
const unauthorized = { code: 'UNAUTHORIZED', message: '로그인이 필요합니다.', detail: null };

/**
 * 목 상태로 **로그인한 척** 할지.
 *
 * 기본은 꺼져 있습니다 — 로그인 화면 자체를 만들 때는 로그아웃 상태여야 합니다.
 * 켜면 세션이 필요한 화면(리허설 준비·발표 연습 등)을 BE 없이 열 수 있습니다.
 *
 *   .env.local 에  VITE_MOCK_SESSION=true      ← 이 프로젝트를 열었을 때의 기본값
 *   브라우저 콘솔에  mockSession.on() / off()   ← 그때그때 뒤집기
 *
 * ── env 만으로는 왜 모자랐나 ────────────────────────────────────────
 * `import.meta.env.X` 는 런타임에 읽는 객체가 아니라 **빌드할 때 값으로 치환**됩니다.
 * 브라우저에 도착하는 코드에는 이미 `"true" === 'true'` 처럼 박제돼 있어서, 콘솔에서
 * 뭘 해도 바뀌지 않습니다. 그래서 로그인 화면과 리허설 화면을 오갈 때마다
 * `.env.local` 수정 → Vite 재시작 → 새로고침을 반복해야 했습니다.
 *
 * env 는 **초기값으로만** 쓰고, 그 뒤로는 아래 `signedIn` 이 진짜 상태입니다.
 *
 * 실서버에 붙을 때(VITE_USE_MOCK=false)는 목 자체가 안 뜨므로 영향이 없습니다.
 */
const OVERRIDE_KEY = 'mock-session';

function initialSignedIn(): boolean {
  // 새로고침을 건너뛰고 살아남아야 합니다 — 토글이 한 번 쓰고 날아가면
  // F5 한 번에 되돌아가서 "왜 또 로그아웃이지" 가 됩니다.
  try {
    const saved = localStorage.getItem(OVERRIDE_KEY);
    if (saved !== null) return saved === 'true';
  } catch {
    // 시크릿 모드 등에서 접근이 막히면 env 기본값으로 갑니다
  }
  return import.meta.env.VITE_MOCK_SESSION === 'true';
}

let signedIn = initialSignedIn();

const mockUser = { id: 'u1', email: 'demo@pitchcoach.local', name: '데모 사용자' };

/**
 * 토큰 회전은 **쿠키를 먼저 봅니다** — `csrf_token` 이 없으면 `refresh()` 가
 * 네트워크를 아예 타지 않습니다 (tokenStore). 목에는 서버가 심어 줄 쿠키가
 * 없으므로 여기서 상태에 맞춰 놓고 지웁니다.
 *
 * ★ 이걸 같이 뒤집지 않으면 `on()` 이 소용없습니다. 핸들러는 로그인 상태로
 *   답할 준비가 됐는데 요청 자체가 안 나가서 계속 로그아웃으로 보입니다.
 */
function setCsrfCookie(on: boolean): void {
  if (typeof document === 'undefined') return;
  document.cookie = on
    ? 'csrf_token=mock-csrf; path=/; SameSite=Lax'
    : 'csrf_token=; path=/; Max-Age=0';
}

setCsrfCookie(signedIn);

declare global {
  interface Window {
    /** 개발 중 목 세션 토글. 프로덕션 번들에는 들어가지 않습니다 */
    mockSession?: { on: () => void; off: () => void };
  }
}

if (import.meta.env.DEV) {
  const set = (on: boolean) => {
    signedIn = on;
    setCsrfCookie(on);
    try {
      localStorage.setItem(OVERRIDE_KEY, String(on));
    } catch {
      // 저장이 막혀도 이번 세션에서는 동작합니다 — 새로고침하면 env 기본값으로 돌아갑니다
    }
    // 메모리에 남은 액세스 토큰과 React Query 캐시까지 한 번에 정리합니다.
    // 변수만 뒤집으면 화면은 그대로라 "껐는데 왜 로그인 상태지" 가 됩니다.
    location.reload();
  };

  window.mockSession = { on: () => set(true), off: () => set(false) };
}

export const authHandlers = [
  // 핸들러는 요청이 올 때마다 `signedIn` 을 읽습니다 — 값을 미리 붙잡지 않으므로
  // 토글이 다음 요청부터 바로 반영됩니다.
  http.post('*/api/auth/refresh', () =>
    signedIn
      ? HttpResponse.json({ access_token: 'mock-access', token_type: 'Bearer', expires_in: 900 })
      : HttpResponse.json(unauthorized, { status: 401 }),
  ),
  http.get('*/api/users/me', () =>
    signedIn ? HttpResponse.json(mockUser) : HttpResponse.json(unauthorized, { status: 401 }),
  ),
  http.post('*/api/auth/logout', () => HttpResponse.json({ detail: '로그아웃되었습니다.' })),
];
