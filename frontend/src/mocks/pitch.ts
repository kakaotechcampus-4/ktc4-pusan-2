import { delay, http, HttpResponse } from 'msw';
import type { PitchRequest, PitchSavedResponse } from '@/types/pitch';

/** BE `PitchSaveRequestDTO` · `PitchUpdateDTO` 의 제목 검사 (1~50자) */
const invalidTitle = (title: string) => title.trim().length === 0 || title.length > 50;

const validationError = () => HttpResponse.json({ code: 'VALIDATION_ERROR' }, { status: 422 });

/** 피치 생성 · 수정 목. BE 처럼 `{"message", "pitch_id"}` 만 돌려줍니다 */
export const pitchHandlers = [
  http.post('*/api/pitches/add', async ({ request }) => {
    const body = (await request.json()) as PitchRequest;
    if (invalidTitle(body.title)) return validationError();
    await delay(400);
    const res: PitchSavedResponse = {
      message: 'Pitch added successfully',
      pitch_id: crypto.randomUUID(),
    };
    return HttpResponse.json(res);
  }),

  // BE #70 — PUT 에서 PATCH 로 바뀌었고, 보낸 필드만 고칩니다
  http.patch('*/api/pitches/update/:pitchId', async ({ request, params }) => {
    const body = (await request.json()) as Partial<PitchRequest>;
    if (body.title === undefined || invalidTitle(body.title)) return validationError();
    await delay(300);
    const res: PitchSavedResponse = {
      message: 'Pitch updated successfully',
      pitch_id: String(params.pitchId),
    };
    return HttpResponse.json(res);
  }),
];
