import { apiRequest } from './client';
import type {
  ServiceReviewReceipt,
  ServiceReviewRequest,
  ServiceReviewState,
  ServiceReviewImpression,
  SuggestionRequest,
  SuggestionReceipt,
} from '@/types/serviceReview';

export const getServiceReviewState = () =>
  apiRequest<ServiceReviewState>('/api/service-feedback/me');
export const requestServiceReview = (display_request_id: string, signal?: AbortSignal) =>
  apiRequest<ServiceReviewImpression>('/api/service-feedback/impressions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_request_id }),
    signal,
  });
export const submitServiceReview = (body: ServiceReviewRequest, signal?: AbortSignal) =>
  apiRequest<ServiceReviewReceipt>('/api/service-feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
export const submitSuggestion = (body: SuggestionRequest, signal?: AbortSignal) =>
  apiRequest<SuggestionReceipt>('/api/suggestions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
