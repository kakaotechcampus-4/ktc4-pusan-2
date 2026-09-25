import { apiBase, ApiFailure, clearTokens, getAccessToken, refresh } from './tokenStore';

/** Only API paths are accepted, so a Bearer token cannot be sent to another origin. */
export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  if (path.startsWith('/api/auth/')) throw new Error('Use the cookie authentication functions');
  if (!path.startsWith('/api/') || path.includes('\\') || path.includes('..'))
    throw new Error('Invalid API path');

  const sentToken = getAccessToken();

  const send = (token: string | null) => {
    const headers = new Headers(options.headers);
    headers.delete('Authorization');
    if (token) headers.set('Authorization', `Bearer ${token}`);

    return fetch(apiBase + path, { ...options, headers, credentials: 'include' });
  };

  let response = await send(sentToken);

  if (response.status === 401) {
    // A late 401 may arrive after another request already rotated the token.
    const current = getAccessToken();
    const token = current && current !== sentToken ? current : await refresh();
    response = await send(token);
  }

  if (!response.ok) {
    if (response.status === 401) clearTokens();

    const body: unknown = await response.json().catch(() => null);
    const code =
      body && typeof body === 'object' && 'code' in body && typeof body.code === 'string'
        ? body.code
        : 'REQUEST_FAILED';

    throw new ApiFailure(response.status, code);
  }

  return response.status === 204 ? (undefined as T) : response.json();
}

/**
 * JSON 본문을 보내는 요청. 경로 검사와 401 재시도, 204 처리는 전부 위
 * `apiRequest` 가 합니다 — 여기서 하는 일은 POST 세 줄을 한 번만 적는 것뿐입니다.
 *
 * 인증 경로(`cookieRequest`)는 이 길을 타지 않습니다. 거기는 JSON 본문이 없고
 * CSRF 헤더와 탭 간 직렬화가 붙으며, `apiRequest` 가 `/api/auth/` 를 거부합니다.
 */
export const postJson = <T>(path: string, body: unknown): Promise<T> =>
  apiRequest<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
