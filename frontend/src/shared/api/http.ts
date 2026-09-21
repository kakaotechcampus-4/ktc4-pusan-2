import { apiRequest } from './client';

/**
 * JSON 본문을 보내는 요청.
 *
 * 경로 검사(`/api/` 로 시작하는지, `/api/auth/` 가 아닌지)와 401 재시도,
 * 204 처리는 전부 `apiRequest` 안에 있습니다 — 여기서 하는 일은 POST 세 줄을
 * 한 번만 적는 것뿐입니다.
 *
 * `fetch` 를 직접 부르는 인증 경로(`cookieRequest`)는 이 길을 타지 않습니다.
 * 거기는 JSON 본문이 없고 CSRF 헤더와 탭 간 직렬화가 붙습니다.
 */
export const postJson = <T>(path: string, body: unknown): Promise<T> =>
  apiRequest<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
