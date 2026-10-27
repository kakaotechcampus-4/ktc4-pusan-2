/**
 * POST /api/pitches/add/{pitch_id}/standards — 자연어 평가기준을 항목으로 나누고, **그 결과를 새 버전으로
 * 저장합니다** (BE #463: 표시 기준 `standards` · 분석 `standard_analyses` 를 같은 버전으로).
 * 사용자가 고친 목록은 `StandardsSaveRequest` 로 다시 저장합니다 (계약 `evaluation-criteria.md` 57행).
 * BE 응답(`StandardTextResponseDTO`) 그대로 snake_case 입니다.
 * 나누는 일은 BE 가 AI(`/evaluation-criteria/parse`)에 맡깁니다. AI 가 실패하면 502 `STANDARD_PARSE_FAILED`.
 */
export interface StandardTextRequest {
  standard_text: string;
}

export interface StandardTextResponse {
  pitch_id: string;
  version: number;
  standards: { standard: string }[];
  /** 기준에 넣지 않은 부분 — AI 가 측정할 수 없다고 본 항목을 쉼표로 이은 것. 없으면 null */
  except_standard: string | null;
}

/**
 * 사용자가 고친 평가기준 저장. 서버는 다시 분석해 늘 새 버전으로 저장합니다 (#532).
 * `POST /api/pitches/{pitch_id}/standards`에 화면 순서대로 문장만 보냅니다.
 */
export interface StandardsSaveRequest {
  standards: { standard: string }[];
}

export type StandardsSaved = StandardTextResponse;

/**
 * GET /api/pitches/{pitch_id}/evaluations/{evaluation_version} — 저장된 평가기준 한 버전.
 * `order` 는 저장한 순서(`position`)입니다.
 */
export interface EvaluationDetail {
  pitch_id: string;
  evaluation_version: number;
  evaluations: { order: number; standard: string }[];
}
