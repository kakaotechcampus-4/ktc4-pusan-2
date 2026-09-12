let accessToken: string | null = null;
let pending: Promise<string> | null = null;
let generation = 0;
export const apiBase = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');
export class ApiFailure extends Error {
  public status: number;
  public code: string;
  constructor(status: number, code: string) {
    super(code);
    this.status = status;
    this.code = code;
  }
}
export function getAccessToken() {
  return accessToken;
}
export function clearTokens() {
  accessToken = null;
  generation++;
}
function csrf() {
  return (
    document.cookie
      .split(';')
      .map((v) => v.trim())
      .find((v) => v.startsWith('csrf_token='))
      ?.slice(11) || ''
  );
}
export async function cookieRequest(path: 'refresh' | 'logout') {
  const send = () =>
    fetch(`${apiBase}/api/auth/${path}`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': csrf() },
    });
  // Cookies are shared across tabs. Serialize rotation using the latest cookie.
  if (typeof navigator !== 'undefined' && navigator.locks) {
    return navigator.locks.request('pitchcoach-auth-cookie', send);
  }
  return send();
}
export function refresh(): Promise<string> {
  if (pending) return pending;
  const version = generation;
  pending = (async () => {
    if (!csrf()) throw new ApiFailure(401, 'UNAUTHORIZED');
    const response = await cookieRequest('refresh');
    if (!response.ok) throw new ApiFailure(response.status, 'REFRESH_FAILED');
    const body = await response.json();
    if (typeof body.access_token !== 'string' || !body.access_token)
      throw new Error('Invalid token response');
    if (version !== generation) throw new ApiFailure(401, 'SESSION_CHANGED');
    accessToken = body.access_token;
    return body.access_token as string;
  })()
    .catch((error) => {
      if (error instanceof ApiFailure && error.status === 401) clearTokens();
      throw error;
    })
    .finally(() => {
      pending = null;
    });
  return pending;
}
export async function logout() {
  // Rotation must finish before logout reads the newest cookie.
  if (pending) await pending;
  const response = await cookieRequest('logout');
  if (!response.ok && response.status !== 401)
    throw new ApiFailure(response.status, 'LOGOUT_FAILED');
  clearTokens();
}
