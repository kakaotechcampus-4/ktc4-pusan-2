import { apiBase, ApiFailure, clearTokens, getAccessToken, refresh } from './tokenStore';

/**
 * Only API paths are accepted, so a Bearer token cannot be sent to another origin.
 * `read` turns a successful body into the result — JSON by default (see `readJsonWithin`).
 */
export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
  read: (response: Response) => Promise<T> = (response) => response.json() as Promise<T>,
): Promise<T> {
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
    // 다른 탭에서 다른 계정으로 로그인해 쿠키가 바뀌었으면 그 토큰으로 다시 보내지 않습니다 —
    // 앞 사용자가 쓴 글이 새 계정으로 접수되지 않게 (chat-agent.md L3-16 "갱신 전후 사용자 ID 가 다르면 재전송 중단")
    if (sentToken && tokenSubject(sentToken) !== tokenSubject(token))
      throw new ApiFailure(401, 'SESSION_CHANGED');
    response = await send(token);
  }

  if (!response.ok) {
    if (response.status === 401) clearTokens();

    const body: unknown = await response.json().catch(() => null);
    const code =
      body && typeof body === 'object' && 'code' in body && typeof body.code === 'string'
        ? body.code
        : 'REQUEST_FAILED';

    throw new ApiFailure(response.status, code, retryAfterSec(response.headers.get('Retry-After')));
  }

  return response.status === 204 ? (undefined as T) : read(response);
}

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

/** 성공 응답이 정한 크기를 넘음 — 본문은 읽지 않고 버립니다 */
export class ResponseTooLargeError extends Error {
  readonly maxBytes: number;

  constructor(maxBytes: number) {
    super('응답이 너무 커요');
    this.name = 'ResponseTooLargeError';
    this.maxBytes = maxBytes;
  }
}

/**
 * 크기(UTF-8 바이트)를 재고 JSON 으로 읽습니다 — 넘으면 `ResponseTooLargeError`.
 * `Content-Length` 가 이미 넘으면 본문을 받지 않습니다. 대화 에이전트의 2 MiB 제한(`chat-agent.md` L3-22)에 씁니다
 */
export const readJsonWithin =
  (maxBytes: number) =>
  async <T>(response: Response): Promise<T> => {
    const declared = Number(response.headers.get('Content-Length'));
    if (declared > maxBytes) {
      await response.body?.cancel().catch(() => undefined);
      throw new ResponseTooLargeError(maxBytes);
    }
    const text = await response.text();
    // UTF-16 한 단위는 UTF-8 로 3바이트까지라(짝 단위 둘은 4바이트), 단위 수가 1/3 이하면 재지 않아도 됩니다
    if (text.length * 3 > maxBytes && new TextEncoder().encode(text).length > maxBytes)
      throw new ResponseTooLargeError(maxBytes);
    return JSON.parse(text) as T;
  };

/**
 * `Retry-After` 를 초로 — 초 숫자와 HTTP 날짜 둘 다 받습니다. 없거나 잘못됐으면 null 이고,
 * 그때 얼마나 기다릴지는 부른 쪽이 정합니다(예: 대화 에이전트는 5초, `chat-agent.md` L3-18).
 */
export function retryAfterSec(value: string | null, now = Date.now()): number | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Number(trimmed);
  // 날짜는 HTTP 형식(IMF-fixdate)만 — 브라우저의 Date.parse 는 "5.5" · "-1" 같은 값도 날짜로 받아 0초가 됩니다
  if (!/^[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$/.test(trimmed))
    return null;
  const at = Date.parse(trimmed);
  if (Number.isNaN(at)) return null;
  return Math.max(0, Math.ceil((at - now) / 1000));
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
