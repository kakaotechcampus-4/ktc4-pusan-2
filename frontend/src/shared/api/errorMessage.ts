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
  /** 요청 값이 BE 의 DTO 검사를 통과하지 못함 (예: 발표 제목 50자 초과) */
  VALIDATION_ERROR: '입력값을 확인해 주세요.',
  PITCH_NOT_FOUND: '발표를 찾을 수 없어요. 발표정보를 다시 저장해 주세요.',
  /** 피치 삭제 — 연습이나 분석이 도는 중이라 거절 (409, BE #1055) */
  PITCH_BUSY: '지금 이 피치로 연습하거나 분석하는 중이에요. 끝난 뒤 다시 삭제해 주세요.',
  /** 대본이 비었거나 50,000자를 넘었거나 쓸 수 없는 문자가 있을 때 */
  INVALID_SCRIPT: '대본을 올리지 못했어요. 비어 있거나 너무 길지 않은지 확인해 주세요.',
  SCRIPT_NOT_FOUND: '대본을 찾을 수 없어요. 대본 매핑을 다시 눌러 주세요.',
  /** 평가기준 정리를 맡은 AI 가 실패했거나 답이 이상할 때 (502) */
  STANDARD_PARSE_FAILED:
    '평가기준을 정리하는 서버가 잠시 응답하지 않아요. 잠시 뒤 다시 정리해 주세요.',
  STANDARD_INPUT_REJECTED:
    '평가기준으로 사용할 수 없는 내용이에요. 측정할 수 있는 발표 기준으로 고쳐 주세요.',
  /** 같은 피치의 평가기준을 이미 정리하는 중 (409). 실패가 아니라 앞 요청을 기다리면 됩니다 */
  STANDARD_PARSE_IN_PROGRESS: '평가기준을 정리하는 중이에요. 끝난 뒤 다시 시도해 주세요.',
  /** 정리 요청이 몰려 받지 못함 (503) */
  STANDARD_PARSE_BUSY: '요청이 많아 평가기준을 정리하지 못했어요. 잠시 뒤 다시 정리해 주세요.',
  EVALUATION_NOT_FOUND: '평가기준을 찾을 수 없어요. 새로고침해 주세요.',
  SCRIPT_ALREADY_PARSED: '이미 나눈 대본이에요. 잠시 뒤 결과가 보여요.',
  /** 발표자료를 BE 가 열지 못함 — PDF 가 아님 · 깨짐 · 여는 암호 · 40MB 초과 (BE #298) */
  INVALID_PRESENTATION: 'PDF를 열 수 없어요. 암호가 걸려 있거나 손상되지 않았는지 확인해 주세요.',
  /** 쪽이나 구성 요소가 너무 많은 PDF — 다시 올려도 같습니다 (BE `presentation_file.py`) */
  PRESENTATION_TOO_COMPLEX: '쪽이나 구성 요소가 너무 많은 PDF예요. 나눠서 올려 주세요.',
  /** 발표자료를 여는 서버가 바쁨 (503). 잠시 뒤 같은 파일로 다시 올리면 됩니다 */
  PRESENTATION_BUSY: '발표자료를 올리는 사람이 많아요. 잠시 뒤 다시 올려 주세요.',
  PRESENTATION_VERSION_NOT_FOUND: '발표자료를 찾을 수 없어요. 다시 올려 주세요.',
  TAKE_NOT_FOUND: '연습 기록을 찾을 수 없어요. 처음부터 다시 시작해 주세요.',
  /** 끝내기를 받았지만 서버가 발표 기록을 아직 저장하지 못함 (503). 같은 값으로 다시 보냅니다 */
  TAKE_RECORDS_NOT_SAVED: '발표 기록을 아직 저장하지 못했어요. 잠시 뒤 다시 보내 주세요.',
  /** 같은 연습 세션으로 다른 모드 · 버전을 보냄 — 다시 보내도 같습니다 */
  TAKE_SESSION_MISMATCH:
    '이 연습은 다른 설정으로 이미 시작했어요. 장치 점검부터 다시 시작해 주세요.',
  /** 대본 확인 — 시간이 지남 · 이미 답함 · 다른 답 반영 중 · 리뷰 시작 (#528) */
  CONFIRMATION_CLOSED: '확인을 받을 수 없어요. 분석 상태를 다시 확인해 주세요.',
  /** rescore 실패 뒤에도 확인 대기 단계는 유지되므로 기한 안에 다시 보낼 수 있습니다 (#528). */
  COVERAGE_RESCORE_FAILED: '답을 반영하지 못했어요. 남은 시간 안에 다시 보내 주세요.',
  INTERNAL_SERVER_ERROR: '서버에 문제가 생겼어요. 잠시 뒤 다시 시도해 주세요.',
  SCRIPT_PARSE_IN_PROGRESS: '대본을 나누는 중이에요. 잠시만 기다려 주세요.',
  SCRIPT_REUPLOAD_REQUIRED: '이 대본은 다시 올려야 해요. 대본 매핑을 다시 눌러 주세요.',
  /** Take 생성 — 나누기가 끝나지 않은 대본으로 시작하려 함 (BE #296) */
  SCRIPT_NOT_PARSED: '대본을 아직 나누는 중이에요. 잠시 뒤 다시 시작해 주세요.',
  /** Take 생성 — 발표자료 쪽수와 대본 장 수가 다름 (BE #298) */
  SLIDE_COUNT_MISMATCH:
    '발표자료 쪽수와 대본 장 수가 달라요. 피치에서 대본 매핑을 다시 확인해 주세요.',
  /** 튜토리얼 샘플 피치 — 동시에 온 다른 요청이 만들자마자 지워짐 (409). 다시 부르면 새로 만듭니다 */
  TUTORIAL_PITCH_BUSY: '다른 요청과 겹쳤어요. 잠시 뒤 다시 시도해 주세요.',
  /** 튜토리얼 리포트는 계정당 한 번 — 받은 뒤 샘플 피치로 Take 를 만들려 함 (409, BE #1135) */
  TUTORIAL_REPORT_USED:
    '튜토리얼 리포트는 한 번만 받을 수 있어요. 내 자료로 새 피치를 만들어 연습해 주세요.',
  /** 앞 튜토리얼 연습을 분석하는 중 — 그 리포트가 나오면 한 번을 쓴 것이라 새 연습을 받지 않습니다 (409) */
  TUTORIAL_ANALYSIS_RUNNING: '앞 연습을 분석하고 있어요. 분석이 끝나면 리포트를 확인해 주세요.',
  REQUEST_FAILED: '요청을 처리하지 못했어요. 잠시 뒤 다시 시도해 주세요.',
};

const FALLBACK = '요청을 처리하지 못했어요. 잠시 뒤 다시 시도해 주세요.';

export function toMessage(error: unknown): string {
  if (error instanceof ApiFailure) return codeMessage(error.code);
  return FALLBACK;
}

/** 응답 본문에 실려 온 에러 코드(장치 점검 준비의 `tutorial_block` 등)를 같은 문구로 바꿉니다 */
export function codeMessage(code: string): string {
  return MESSAGE[code] ?? FALLBACK;
}
