export type SuggestionCategory = 'POSITIVE' | 'IMPROVEMENT' | 'PROBLEM' | 'QUESTION' | 'OTHER';

// 1차 배포의 별점 팝업 타입은 BE #777 에서 API 가 없어지며 지웠습니다. 리포트 설문은 `takeSurvey.ts` 입니다
export interface SuggestionRequest {
  request_id: string;
  content: string;
  category?: SuggestionCategory;
}
export interface SuggestionReceipt {
  id: string;
  source: 'MY_PAGE';
  category: SuggestionCategory;
  created_at: string;
}
