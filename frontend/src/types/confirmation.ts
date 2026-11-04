import type { AnalysisStage } from './report';

/**
 * 대본 전달도 확인 — 발표 뒤 인식이 헷갈린 말(비슷한 말)을 사용자에게 묻습니다.
 * BE #528의 GET · POST `/api/takes/{take_id}/confirmations` 응답 계약입니다.
 *
 * 필드 이름은 AI 계약 그대로입니다 (`docs/ai/features/script-coverage-evaluation.md` 5-3 `confirmations[]`,
 * 5-6 `similar_items[]`). 답은 리뷰를 부르기 전까지만 받고, 확인 대기가 시작된 지 10분이 지나면
 * 답 없이 그대로 진행됩니다 (같은 문서 12절, `architecture.md` 1-3).
 */

/** 5-4 확인 답 — 대본대로 말함(STT 가 잘못 적음) · 들린 대로 말함(발표자가 다른 말을 함) */
export type ConfirmAnswer = 'as_script' | 'as_stt';

/** `GET /api/takes/{take_id}/confirmations` */
export interface Confirmations {
  take_id: string;
  /** 지금 분석 단계. `AWAITING_CONFIRMATION` 이 아니면 이미 넘어가 `items` 가 빕니다 */
  analysis_stage: AnalysisStage | null;
  /**
   * 답을 받을 수 있는 남은 시간. BE 가 `analysis_stage_at + 10분` 에서 계산합니다 — 사용자 컴퓨터
   * 시계가 틀려도 어긋나지 않게 시각이 아니라 남은 ms 로 받습니다. 확인 대기가 아니면 null
   */
  remaining_ms: number | null;
  items: ConfirmationItem[];
}

/**
 * 확인 항목 하나. 앞 네 필드는 5-3 `confirmations[]`, `kind` 는 5-6 `similar_items[]` 입니다.
 * 문장 둘은 BE 가 채점에서 찾아 붙입니다 — 대본 문장은 평가 기준
 * `sentences[script_sentence_index].text`, 인식된 문장은 채점 `stt_sentences[stt_sentence_index]`.
 *
 * `item_id` 는 장마다 R1 부터 다시 세서 `(slide_number, item_id)` 둘이 함께 키입니다.
 * 녹음 다시 듣기(`stt_raw_span`)는 싣지 않습니다 — 음성을 어디에도 저장하지 않습니다 (CLAUDE.md 4번).
 */
export interface ConfirmationItem {
  slide_number: number;
  item_id: string;
  /** 같은 장의 similar_items 항목을 못 찾으면 null. BE 는 문자열 값을 그대로 돌려줍니다. */
  kind: string | null;
  script_text: string;
  stt_text: string;
  script_sentence: string | null;
  stt_sentence: string | null;
}

/**
 * `POST /api/takes/{take_id}/confirmations`. 5-2 `/takes/rescore` 의 `answers` 와 같은 모양입니다.
 * 빈 배열이면 건너뛰기 — 답 없이 리뷰로 넘어갑니다. 고르지 않은 항목은 보류로 남습니다 (6-3).
 * 확인 대기가 끝났으면(시간 초과 · 이미 답함 · 리뷰 시작) 409 `CONFIRMATION_CLOSED`.
 */
export interface ConfirmationsRequest {
  answers: { slide_number: number; item_id: string; answer: ConfirmAnswer }[];
}

export interface ConfirmationsSent {
  analysis_stage: AnalysisStage;
}
