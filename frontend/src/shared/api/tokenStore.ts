let accessToken: string | null = null;
let pending: Promise<string> | null = null;
let generation = 0;
/**
 * 이 페이지가 마지막으로 받아들인 사용자(토큰의 `sub`). 토큰을 지워도(401) 남겨 두고, 로그아웃할 때만 비웁니다 —
 * 다른 탭에서 다른 계정으로 로그인해 쿠키가 바뀌면 그 계정의 토큰을 받아들이지 않습니다(chat-agent.md L3-16).
 * 계정을 바꾸는 길(로그인 · 탈퇴)은 페이지를 새로 엽니다
 */
let knownSubject: string | null = null;

/** 액세스 토큰(JWT)의 사용자(`sub`) — 읽을 수 없으면 null (서명은 서버가 봅니다) */
export function tokenSubject(token: string | null): string | null {
  const payload = token?.split('.')[1];
  if (!payload) return null;
  try {
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/');
    const body: unknown = JSON.parse(atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, '=')));
    return body && typeof body === 'object' && 'sub' in body && typeof body.sub === 'string'
      ? body.sub
      : null;
  } catch {
    return null;
  }
}

export const apiBase = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');

export class ApiFailure extends Error {
  public status: number;
  public code: string;
  /** 서버가 `Retry-After` 로 알린 기다릴 초. 없거나 읽을 수 없으면 null — 429 를 다루는 화면이 씁니다 */
  public retryAfterSec: number | null;
  /**
   * 서버 응답의 `message`. 없으면 null. 화면에 그대로 보이지 않습니다 — 같은 코드 안에서 사유를 가려야 할 때만
   * 정해진 문구와 견줍니다(예: 되돌리기 409 의 "기준 기록 삭제")
   */
  public serverMessage: string | null;

  constructor(
    status: number,
    code: string,
    retryAfterSec: number | null = null,
    serverMessage: string | null = null,
  ) {
    super(code);
    this.status = status;
    this.code = code;
    this.retryAfterSec = retryAfterSec;
    this.serverMessage = serverMessage;
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

    // 다른 계정의 토큰은 메모리에 넣지 않습니다 — 넣으면 다음 요청부터 그 계정으로 나갑니다
    const subject = tokenSubject(body.access_token);
    if (knownSubject !== null && subject !== null && subject !== knownSubject)
      throw new ApiFailure(401, 'SESSION_CHANGED');
    accessToken = body.access_token;
    if (subject !== null) knownSubject = subject;
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
  knownSubject = null;
}
