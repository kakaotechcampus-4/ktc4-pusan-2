/**
 * POST /api/pitches/add/{pitch_id}/standards — 자연어 평가기준을 항목으로 나눕니다. **저장하지 않습니다** (BE #192).
 * 사용자가 고친 뒤 `POST /api/pitches/{pitch_id}/standards` 로 확정해야 저장됩니다 (`StandardsSaveRequest`).
 * BE 응답 그대로 snake_case 입니다.
 *
 * ★ 컨트롤러가 서비스 결과(`StandardTextResponseDTO`)를 `{"message", "pitch_id": <DTO>}` 로
 *   한 번 더 감싸 돌려줍니다. 그래서 나눈 결과는 `pitch_id` 키 **안에** 있습니다 —
 *   `StandardsPosted` 가 그 모양 그대로입니다.
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

/** 컨트롤러가 실제로 돌려주는 모양 */
export interface StandardsPosted {
  message: string;
  /** 이름은 pitch_id 지만 나눈 결과가 들어 있습니다 */
  pitch_id: StandardTextResponse;
}

/**
 * POST /api/pitches/{pitch_id}/standards — 사용자가 고친 평가기준을 확정해 저장합니다 (BE #192 `StandardsSaveDTO`).
 * 순서대로 저장되고(`position`), 문장만 보냅니다.
 */
export interface StandardsSaveRequest {
  standards: { standard: string }[];
}

export interface StandardsSaved {
  message: string;
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
