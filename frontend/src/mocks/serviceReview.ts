import { delay, http, HttpResponse } from 'msw';
import type { ServiceReviewReceipt } from '@/types/serviceReview';

// Mock-only URL, deliberately separate from the AI review API.
const receipts = new Map<string, ServiceReviewReceipt>();
const failedOnce = new Set<string>();
export const serviceReviewHandlers = [
  http.post('*/api/mock/service-reviews/:takeId', async ({ params, request }) => {
    await delay(400);
    const takeId = String(params.takeId);
    const body = (await request.json().catch(() => null)) as { rating?: unknown } | null;
    if (
      !body ||
      !Number.isInteger(body.rating) ||
      Number(body.rating) < 1 ||
      Number(body.rating) > 5
    )
      return HttpResponse.json({ code: 'INVALID_RATING' }, { status: 422 });
    // A retry fixture independent of the selected rating.
    if (takeId === 't-review-retry' && !failedOnce.has(takeId)) {
      failedOnce.add(takeId);
      return HttpResponse.json({ code: 'REVIEW_UNAVAILABLE' }, { status: 503 });
    }
    const receipt = receipts.get(takeId) ?? { take_id: takeId, rating: Number(body.rating) };
    receipts.set(takeId, receipt);
    return HttpResponse.json(receipt);
  }),
];
