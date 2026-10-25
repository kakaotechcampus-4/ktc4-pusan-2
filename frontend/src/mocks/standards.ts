import { delay, http, HttpResponse } from 'msw';
import type {
  StandardTextRequest,
  StandardTextResponse,
  StandardsSaveRequest,
  StandardsSaved,
} from '@/types/standards';

/**
 * 평가기준 나누기 목. 실서버는 AI 로 나눌 예정이고, 목은 **줄 · 문장 단위**로 자릅니다.
 *
 * 결과 모양만 맞추면 됩니다 — FE 는 `standards` 를 목록으로, `except_standard` 를
 * 안내 문구로 그릴 뿐이라 나누는 품질은 화면 흐름 확인에 상관없습니다.
 */

export function splitStandards(text: string): string[] {
  return (
    text
      .split(/\n+/)
      // "1." "-" "•" 같은 머리표를 뗍니다
      .map((line) => line.replace(/^\s*(?:\d+[.)]|[-•*·])\s*/, ''))
      .flatMap((line) => line.split(/(?<=[.!?。])\s+/))
      .map((s) => s.trim().replace(/[.。]$/, ''))
      .filter((s) => s.length >= 2)
  );
}

export const standardsHandlers = [
  http.post('*/api/pitches/add/:pitchId/standards', async ({ request, params }) => {
    const { standard_text } = (await request.json()) as StandardTextRequest;
    await delay(900);

    // BE 처럼 개수를 자르지 않고 모두 돌려줍니다. 목은 나누지 못한 문장을 따로 가리지 않습니다
    const body: StandardTextResponse = {
      pitch_id: String(params.pitchId),
      standards: splitStandards(standard_text).map((standard) => ({ standard })),
      except_standard: null,
    };
    return HttpResponse.json(body);
  }),

  // 확정 저장 (BE #192). 나누기와 달리 받은 목록을 그대로 저장합니다 — 목은 저장한 척만 합니다
  http.post('*/api/pitches/:pitchId/standards', async ({ request }) => {
    const { standards } = (await request.json()) as StandardsSaveRequest;
    await delay(400);
    if (standards.length === 0 || standards.some((s) => s.standard.trim() === '')) {
      return HttpResponse.json(
        { code: 'VALIDATION_ERROR', message: '빈 평가기준은 저장할 수 없습니다.' },
        { status: 422 },
      );
    }
    const body: StandardsSaved = { message: 'Final standards added successfully' };
    return HttpResponse.json(body);
  }),
];
