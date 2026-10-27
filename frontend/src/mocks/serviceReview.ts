import { http, HttpResponse } from 'msw';
import { currentMockUserId } from './auth';
import { homeFixture } from './home';
import type { ServiceReviewReceipt, SuggestionReceipt } from '@/types/serviceReview';

const uuid = (v: unknown): v is string =>
  typeof v === 'string' &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);
const opinion = (v: unknown): v is string =>
  typeof v === 'string' &&
  ![...v].some((c) => {
    const code = c.codePointAt(0)!;
    return code >= 0xd800 && code <= 0xdfff;
  }) &&
  !v.includes('\0') &&
  [...v.trim()].length <= 2000;
const error = (code: string, status = 409) =>
  HttpResponse.json({ code, message: code, detail: null }, { status });

// shortcut: MSW 메모리의 사용자별 상태입니다. 탭·기기 간 경쟁 검증은 실제 BE로 수행합니다.
export function createServiceReviewHandlers(
  userId = currentMockUserId,
  hasReport = () => homeFixture.pitches.some((p) => p.takes.some((t) => t.status === 'COMPLETED')),
) {
  const users = new Map<
    string,
    {
      displays: Set<string>;
      review?: ServiceReviewReceipt;
      opinion: string;
      suggestions: Map<string, { content: string; receipt: SuggestionReceipt }>;
    }
  >();
  const state = (id: string) => {
    if (!users.has(id)) users.set(id, { displays: new Set(), opinion: '', suggestions: new Map() });
    return users.get(id)!;
  };
  return [
    http.get('*/api/service-feedback/me', () => {
      const id = userId();
      if (!id) return error('UNAUTHORIZED', 401);
      const s = state(id);
      const report = hasReport();
      return HttpResponse.json({
        has_report: report,
        submitted: Boolean(s.review),
        display_count: s.displays.size,
        display_limit: 3,
        should_prompt: report && !s.review && s.displays.size < 3,
      });
    }),
    http.post('*/api/service-feedback/impressions', async ({ request }) => {
      const body = (await request.json().catch(() => null)) as Record<string, unknown> | null;
      const id = userId();
      if (!id) return error('UNAUTHORIZED', 401);
      if (
        !body ||
        Object.keys(body).some((k) => k !== 'display_request_id') ||
        !uuid(body.display_request_id)
      )
        return error('VALIDATION_ERROR', 422);
      const s = state(id);
      let reason: string | null = null;
      if (s.review) reason = 'ALREADY_SUBMITTED';
      else if (!hasReport()) reason = 'NO_REPORT';
      else if (!s.displays.has(body.display_request_id) && s.displays.size >= 3)
        reason = 'DISPLAY_LIMIT_REACHED';
      if (!reason) s.displays.add(body.display_request_id);
      return HttpResponse.json({
        should_open: !reason,
        submitted: Boolean(s.review),
        display_count: s.displays.size,
        display_limit: 3,
        reason,
      });
    }),
    http.post('*/api/service-feedback', async ({ request }) => {
      const body = (await request.json().catch(() => null)) as Record<string, unknown> | null;
      const id = userId();
      if (!id) return error('UNAUTHORIZED', 401);
      if (
        !body ||
        Object.keys(body).some((k) => !['rating', 'suggestion'].includes(k)) ||
        !Number.isInteger(body.rating) ||
        Number(body.rating) < 1 ||
        Number(body.rating) > 5 ||
        (body.suggestion != null && !opinion(body.suggestion))
      )
        return error('VALIDATION_ERROR', 422);
      const s = state(id);
      const text = typeof body.suggestion === 'string' ? body.suggestion.trim() : '';
      if (s.review)
        return s.review.rating === body.rating && s.opinion === text
          ? HttpResponse.json(s.review)
          : error('SERVICE_FEEDBACK_ALREADY_SUBMITTED');
      if (!hasReport()) return error('SERVICE_FEEDBACK_NOT_ELIGIBLE');
      s.review = {
        submitted: true,
        rating: Number(body.rating),
        created_at: new Date().toISOString(),
      };
      s.opinion = text;
      return HttpResponse.json(s.review, { status: 201 });
    }),
    http.post('*/api/suggestions', async ({ request }) => {
      const body = (await request.json().catch(() => null)) as Record<string, unknown> | null;
      const id = userId();
      if (!id) return error('UNAUTHORIZED', 401);
      if (
        !body ||
        Object.keys(body).some((k) => !['request_id', 'content'].includes(k)) ||
        !uuid(body.request_id) ||
        !opinion(body.content) ||
        !body.content.trim()
      )
        return error('VALIDATION_ERROR', 422);
      const s = state(id);
      const content = body.content.trim();
      const previous = s.suggestions.get(body.request_id);
      if (previous)
        return previous.content === content
          ? HttpResponse.json(previous.receipt)
          : error('SUGGESTION_REQUEST_CONFLICT');
      const receipt: SuggestionReceipt = {
        id: crypto.randomUUID(),
        source: 'MY_PAGE',
        created_at: new Date().toISOString(),
      };
      s.suggestions.set(body.request_id, { content, receipt });
      return HttpResponse.json(receipt, { status: 201 });
    }),
  ];
}
export const serviceReviewHandlers = createServiceReviewHandlers();
