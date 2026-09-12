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
