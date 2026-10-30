import { useQuery } from '@tanstack/react-query';
import { apiRequest, postJson } from './client';
import type {
  PendingSurveys,
  TakeSurvey,
  TakeSurveyRequest,
  TakeSurveyState,
} from '@/types/takeSurvey';

/** 리포트 아래 설문 버튼이 제출 여부를 봅니다. 홈 안내 목록은 `takeSurveyKeys.pending` 입니다 */
export const takeSurveyKeys = {
  state: (takeId: string) => ['take-survey', takeId] as const,
  pending: ['take-surveys', 'pending'] as const,
};

export const getTakeSurvey = (takeId: string) =>
  apiRequest<TakeSurveyState>(`/api/takes/${takeId}/survey`);

/**
 * 처음이면 201, 같은 답을 다시 보내면 200 입니다. 다른 답이면 409 `TAKE_SURVEY_ALREADY_SUBMITTED`,
 * 리포트가 없는 Take 면 409 `TAKE_SURVEY_NOT_ELIGIBLE` 입니다. 실패하면 같은 답으로 다시 보내면 됩니다
 */
export const submitTakeSurvey = (takeId: string, body: TakeSurveyRequest) =>
  postJson<TakeSurvey>(`/api/takes/${takeId}/survey`, body);

export function useTakeSurvey(takeId: string, enabled: boolean) {
  return useQuery({
    queryKey: takeSurveyKeys.state(takeId),
    queryFn: () => getTakeSurvey(takeId),
    enabled,
    retry: false,
  });
}

/** 홈 안내. 동의하지 않았으면 BE 가 빈 목록을 줍니다 */
export function usePendingSurveys(enabled: boolean) {
  return useQuery({
    queryKey: takeSurveyKeys.pending,
    queryFn: () => apiRequest<PendingSurveys>('/api/take-surveys/pending'),
    enabled,
    retry: false,
  });
}
