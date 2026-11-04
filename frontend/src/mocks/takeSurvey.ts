import { http, HttpResponse } from 'msw';
import { currentMockUserId, mockTestConsentAt } from './auth';
import { homeFixture } from './home';
import type { PendingSurvey, TakeSurvey } from '@/types/takeSurvey';

const error = (code: string, status = 409) =>
  HttpResponse.json({ code, message: code, detail: null }, { status });
const rating = (v: unknown) => Number.isInteger(v) && Number(v) >= 1 && Number(v) <= 5;
const FIELDS = ['accuracy_rating', 'usefulness_rating', 'comment', 'question_version'];

/** 홈 목록의 Take. 목록에 없는 Take(리포트 목의 예시 ID 등)는 끝난 것으로 봅니다 */
function fixtureTake(takeId: string) {
  for (const pitch of homeFixture.pitches)
    for (const take of pitch.takes) if (take.take_id === takeId) return { pitch, take };
  return null;
}

/**
 * 리포트마다 받는 설문 (#776 · BE #777, `docs/be/take-survey.md` 3절).
 * shortcut: 목에는 Take 가 끝난 시각이 없어, 동의한 뒤 끝난 Take 대신 동의했으면 끝난 Take 를 모두 미작성 목록에 넣습니다.
 */
export function createTakeSurveyHandlers(
  userId = currentMockUserId,
  consentedAt: () => string | null = mockTestConsentAt,
) {
  const saved = new Map<string, TakeSurvey>();
  const key = (user: string, takeId: string) => `${user}:${takeId}`;
  return [
    http.get('*/api/take-surveys/pending', () => {
      const id = userId();
      if (!id) return error('UNAUTHORIZED', 401);
      if (!consentedAt()) return HttpResponse.json({ items: [] });
      const items: PendingSurvey[] = homeFixture.pitches.flatMap((pitch) =>
        pitch.takes
          .filter((take) => take.status === 'COMPLETED' && !saved.has(key(id, take.take_id)))
          .map((take) => ({
            take_id: take.take_id,
            pitch_id: pitch.pitch_id,
            pitch_title: pitch.pitch_title,
            take_number: take.take_version,
            completed_at: new Date().toISOString(),
          })),
      );
      return HttpResponse.json({ items });
    }),
    http.get('*/api/takes/:takeId/survey', ({ params }) => {
      const id = userId();
      if (!id) return error('UNAUTHORIZED', 401);
      const survey = saved.get(key(id, String(params.takeId))) ?? null;
      return HttpResponse.json({ submitted: survey !== null, survey });
    }),
    http.post('*/api/takes/:takeId/survey', async ({ params, request }) => {
      const id = userId();
      if (!id) return error('UNAUTHORIZED', 401);
      const takeId = String(params.takeId);
      const body = (await request.json().catch(() => null)) as Record<string, unknown> | null;
      const comment = typeof body?.comment === 'string' ? body.comment.trim() || null : null;
      if (
        !body ||
        Object.keys(body).some((k) => !FIELDS.includes(k)) ||
        !rating(body.accuracy_rating) ||
        !rating(body.usefulness_rating) ||
        (body.comment != null && typeof body.comment !== 'string') ||
        [...(comment ?? '')].length > 2000 ||
        (body.question_version !== undefined &&
          !(Number.isInteger(body.question_version) && Number(body.question_version) >= 1))
      )
        return error('VALIDATION_ERROR', 422);
      const found = fixtureTake(takeId);
      if (found && found.take.status !== 'COMPLETED') return error('TAKE_SURVEY_NOT_ELIGIBLE');
      const answer = {
        accuracy_rating: Number(body.accuracy_rating),
        usefulness_rating: Number(body.usefulness_rating),
        comment,
        question_version: Number(body.question_version ?? 1),
      };
      const previous = saved.get(key(id, takeId));
      if (previous) {
        const same = (Object.keys(answer) as (keyof typeof answer)[]).every(
          (k) => previous[k] === answer[k],
        );
        return same ? HttpResponse.json(previous) : error('TAKE_SURVEY_ALREADY_SUBMITTED');
      }
      const survey: TakeSurvey = {
        take_id: takeId,
        ...answer,
        created_at: new Date().toISOString(),
      };
      saved.set(key(id, takeId), survey);
      return HttpResponse.json(survey, { status: 201 });
    }),
  ];
}
export const takeSurveyHandlers = createTakeSurveyHandlers();
