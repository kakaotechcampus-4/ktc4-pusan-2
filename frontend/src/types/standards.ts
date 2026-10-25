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
  standards: { standard: string }[];
  /** 기준에 넣지 않은 부분 — AI 가 측정할 수 없다고 본 항목을 쉼표로 이은 것. 없으면 null */
  except_standard: string | null;
}

/**
 * 사용자가 고친 평가기준 저장. 계약은 "고친 표시 기준을 저장하면 서버가 그 글로 한 번 더 분석해 같은 버전으로
 * 저장한다"이고, **이 호출의 경로 · 모양은 아직 정하지 않았습니다** (`evaluation-criteria.md` 304행).
 * 지금은 FE 가 제안한 `POST /api/pitches/{pitch_id}/standards` 입니다. 순서대로(`position`) 문장만 보냅니다.
 */
export interface StandardsSaveRequest {
  standards: { standard: string }[];
}

export interface StandardsSaved {
  /**
   * 다시 분석해 저장한 표시 기준. 보낸 문장과 다를 수 있어 화면은 이것으로 다시 그립니다.
   * CLAUDE.md 6번(`?` 대신 `| null`)의 예외입니다 — 응답 모양이 아직 정해지지 않아(304행) 서버가 주지 않을
   * 수 있습니다. 없으면 보낸 목록을 씁니다
   */
  standards?: { standard: string }[];
}

/**
 * GET /api/pitches/{pitch_id}/evaluations/{evaluation_version} — 저장된 평가기준 한 버전.
 * `order` 는 저장한 순서(`position`)입니다.
 */
export interface EvaluationDetail {
  pitch_id: string;
  evaluation_version: number;
  evaluations: { order: number; standard: string }[];
}
