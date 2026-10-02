/**
 * POST /api/pitches/add/{pitch_id}/standards — 자연어 평가기준을 항목으로 나눕니다.
 * BE 응답 그대로 snake_case 입니다 (`StandardTextResponseDTO`).
 *
 * ★ BE 의 나누기 로직은 아직 주석 상태입니다(`add_pitch_standard_service`). 그리고 컨트롤러가
 *   이 DTO 를 `{"message", "pitch_id": <DTO>}` 로 한 번 더 감싸고 있어, 지금 그대로면
 *   `standards` 가 `pitch_id` 안에 들어갑니다. FE 는 서비스 DTO 모양을 따릅니다 — BE 확인 필요.
 */
export interface StandardTextRequest {
  standard_text: string;
}

export interface StandardTextResponse {
  pitch_id: string;
  standards: { standard: string }[];
  /** 기준으로 만들지 못한 부분. 다 들어갔으면 null */
  except_standard: string | null;
}
