import { postJson } from './client';
import type { ServiceReviewReceipt, ServiceReviewRequest } from '@/types/serviceReview';

// Keep the unagreed endpoint out of production and real-server development.
export const serviceReviewPreview =
  import.meta.env.DEV && import.meta.env.VITE_USE_MOCK !== 'false';

export function submitServiceReview(takeId: string, body: ServiceReviewRequest) {
  if (!serviceReviewPreview) return Promise.reject(new Error('Service review preview is disabled'));
  return postJson<ServiceReviewReceipt>(
    `/api/mock/service-reviews/${encodeURIComponent(takeId)}`,
    body,
  );
}
