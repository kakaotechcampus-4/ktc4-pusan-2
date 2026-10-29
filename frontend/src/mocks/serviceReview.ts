import { http, HttpResponse } from 'msw';
import { currentMockUserId } from './auth';
import { suggestionCategories } from '@/shared/lib/suggestionCategory';
import type { SuggestionCategory, SuggestionReceipt } from '@/types/serviceReview';

const validCategory = (v: unknown): v is SuggestionCategory =>
  typeof v === 'string' && Object.hasOwn(suggestionCategories, v);

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
// 1차 배포의 별점 팝업(`/api/service-feedback*`)은 BE #777 에서 없어져 마이페이지 의견만 남깁니다
export function createServiceReviewHandlers(userId = currentMockUserId) {
  const users = new Map<string, Map<string, { content: string; receipt: SuggestionReceipt }>>();
  const suggestions = (id: string) => {
    if (!users.has(id)) users.set(id, new Map());
    return users.get(id)!;
  };
  return [
    http.post('*/api/suggestions', async ({ request }) => {
      const body = (await request.json().catch(() => null)) as Record<string, unknown> | null;
      const id = userId();
      if (!id) return error('UNAUTHORIZED', 401);
      if (
        !body ||
        Object.keys(body).some((k) => !['request_id', 'content', 'category'].includes(k)) ||
        (body.category !== undefined && !validCategory(body.category)) ||
        !uuid(body.request_id) ||
        !opinion(body.content) ||
        !body.content.trim()
      )
        return error('VALIDATION_ERROR', 422);
      const saved = suggestions(id);
      const content = body.content.trim();
      const category = body.category ?? 'OTHER';
      const previous = saved.get(body.request_id);
      if (previous)
        return previous.content === content && previous.receipt.category === category
          ? HttpResponse.json(previous.receipt)
          : error('SUGGESTION_REQUEST_CONFLICT');
      const receipt: SuggestionReceipt = {
        id: crypto.randomUUID(),
        source: 'MY_PAGE',
        category,
        created_at: new Date().toISOString(),
      };
      saved.set(body.request_id, { content, receipt });
      return HttpResponse.json(receipt, { status: 201 });
    }),
  ];
}
export const serviceReviewHandlers = createServiceReviewHandlers();
