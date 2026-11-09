import { apiRequest } from './client';
import type { SuggestionRequest, SuggestionReceipt } from '@/types/serviceReview';

// 1차 배포의 홈 별점 팝업 API(`/api/service-feedback*`)는 BE #777 에서 없어졌습니다.
// 리포트마다 받는 설문은 `takeSurvey.ts` 입니다. 여기에는 마이페이지 의견 보내기만 남습니다
export const submitSuggestion = (body: SuggestionRequest, signal?: AbortSignal) =>
  apiRequest<SuggestionReceipt>('/api/suggestions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
