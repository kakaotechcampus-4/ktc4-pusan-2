import { http, HttpResponse } from 'msw';

// Default mocks remain unauthenticated; Google navigation is never faked.
const unauthorized = { code: 'UNAUTHORIZED', message: '로그인이 필요합니다.', detail: null };
export const authHandlers = [
  http.post('*/api/auth/refresh', () => HttpResponse.json(unauthorized, { status: 401 })),
  http.get('*/api/users/me', () => HttpResponse.json(unauthorized, { status: 401 })),
  http.post('*/api/auth/logout', () => HttpResponse.json({ detail: '로그아웃되었습니다.' })),
];
