import { delay, http, HttpResponse } from 'msw';
import type { PitchRequest, PitchSavedResponse } from '@/types/pitch';

/** 피치 생성 · 수정 목. BE 처럼 `{"message", "pitch_id"}` 만 돌려줍니다 */
export const pitchHandlers = [
  http.post('*/api/pitches/add', async ({ request }) => {
    const body = (await request.json()) as PitchRequest;
    if (!body.title.trim()) return HttpResponse.json({ code: 'BAD_REQUEST' }, { status: 400 });
    await delay(400);
    const res: PitchSavedResponse = {
      message: 'Pitch added successfully',
      pitch_id: crypto.randomUUID(),
    };
    return HttpResponse.json(res);
  }),

  http.put('*/api/pitches/update/:pitchId', async ({ params }) => {
    await delay(300);
    const res: PitchSavedResponse = {
      message: 'Pitch updated successfully',
      pitch_id: String(params.pitchId),
    };
    return HttpResponse.json(res);
  }),
];
