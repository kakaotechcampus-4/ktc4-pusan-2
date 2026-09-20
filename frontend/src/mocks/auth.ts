import { http, HttpResponse } from 'msw';

// Default mocks remain unauthenticated; Google navigation is never faked.
const unauthorized = { code: 'UNAUTHORIZED', message: '로그인이 필요합니다.', detail: null };

/**
 * 목 상태로 **로그인한 척** 할지.
 *
 * 기본은 꺼져 있습니다 — 로그인 화면 자체를 만들 때는 로그아웃 상태여야 합니다.
 * 켜면 세션이 필요한 화면(리허설 준비·발표 연습 등)을 BE 없이 열 수 있습니다.
 *
 *   .env.local 에  VITE_MOCK_SESSION=true
 *
 * 실서버에 붙을 때(VITE_USE_MOCK=false)는 목 자체가 안 뜨므로 영향이 없습니다.
 */
const MOCK_SESSION = import.meta.env.VITE_MOCK_SESSION === 'true';

const mockUser = { id: 'u1', email: 'demo@pitchcoach.local', name: '데모 사용자' };

/**
 * 토큰 회전은 **쿠키를 먼저 봅니다** (`csrf_token` 이 없으면 refresh 를 아예 안 부릅니다).
 * 목에는 서버가 심어 줄 쿠키가 없으므로 여기서 하나 놓아 둡니다.
 */
if (MOCK_SESSION && typeof document !== 'undefined' && !document.cookie.includes('csrf_token=')) {
  document.cookie = 'csrf_token=mock-csrf; path=/; SameSite=Lax';
}

export const authHandlers = [
  http.post('*/api/auth/refresh', () =>
    MOCK_SESSION
      ? HttpResponse.json({ access_token: 'mock-access', token_type: 'Bearer', expires_in: 900 })
      : HttpResponse.json(unauthorized, { status: 401 }),
  ),
  http.get('*/api/users/me', () =>
    MOCK_SESSION ? HttpResponse.json(mockUser) : HttpResponse.json(unauthorized, { status: 401 }),
  ),
  http.post('*/api/auth/logout', () => HttpResponse.json({ detail: '로그아웃되었습니다.' })),
];
