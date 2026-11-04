/**
 * 리포트마다 받는 설문 (#776 · BE #777, `docs/be/take-survey.md`). BE 그대로 snake_case 입니다.
 * 1차 배포의 홈 별점 팝업(#540)을 대신합니다.
 */
export interface TakeSurveyRequest {
  /** 분석 결과의 체감 정확도 1~5 */
  accuracy_rating: number;
  /** 피드백의 구체성 · 유용성 1~5 */
  usefulness_rating: number;
  /** 부정확하거나 아쉬웠던 점. 비우면 null 로 보냅니다 (BE 가 공백만인 글도 null 로 봅니다) */
  comment: string | null;
  /** 화면에 보여 준 문항 문구의 버전. 문구를 고치면 올립니다 */
  question_version: number;
}

export interface TakeSurvey extends TakeSurveyRequest {
  take_id: string;
  created_at: string;
}

/** `GET /api/takes/{id}/survey`. 제출 전이면 survey 가 null 입니다 */
export interface TakeSurveyState {
  submitted: boolean;
  survey: TakeSurvey | null;
}

/** `GET /api/take-surveys/pending` 한 줄. 테스트 참여에 동의한 뒤 끝난 Take 만 옵니다 */
export interface PendingSurvey {
  take_id: string;
  pitch_id: string;
  pitch_title: string;
  take_number: number;
  completed_at: string;
}

export interface PendingSurveys {
  items: PendingSurvey[];
}
