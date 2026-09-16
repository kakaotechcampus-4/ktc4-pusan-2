import { ApiFailure } from './tokenStore';

/**
 * 서버 에러 코드 → 화면 문구.
 *
 * BE 는 모든 에러를 `{code, message, detail}` 로 통일해 내려주고,
 * **프론트는 `code` 로 분기합니다** (backend/README.md 의 에러 응답 형식).
 * `apiRequest` 는 code 만 들고 오므로 문구는 여기서 붙입니다 —
 * 화면마다 새로 지으면 같은 상황에서 사람마다 다른 말이 나옵니다.
 */
const MESSAGE: Record<string, string> = {
  BAD_REQUEST: '요청이 올바르지 않아요.',
  UNAUTHORIZED: '로그인이 필요해요. 다시 로그인해 주세요.',
  FORBIDDEN: '이 작업을 할 권한이 없어요.',
  NOT_FOUND: '찾을 수 없어요. 새로고침해 주세요.',
  METHOD_NOT_ALLOWED: '처리할 수 없는 요청이에요.',
  REFRESH_FAILED: '로그인이 만료됐어요. 다시 로그인해 주세요.',
  SESSION_CHANGED: '다른 곳에서 로그인했어요. 다시 로그인해 주세요.',
  /** 시선 구간의 형식이 어긋났을 때. 서버는 값을 재계산할 수 없어 형식만 봅니다 */
  INVALID_SEGMENTS: '시선 기록 형식이 올바르지 않아 서버가 받지 못했어요.',
  REQUEST_FAILED: '요청을 처리하지 못했어요. 잠시 뒤 다시 시도해 주세요.',
};

const FALLBACK = '요청을 처리하지 못했어요. 잠시 뒤 다시 시도해 주세요.';

export function toMessage(error: unknown): string {
  if (error instanceof ApiFailure) return MESSAGE[error.code] ?? FALLBACK;
  return FALLBACK;
}
