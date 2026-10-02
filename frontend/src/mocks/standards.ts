import { delay, http, HttpResponse } from 'msw';
import type { StandardTextRequest, StandardTextResponse } from '@/types/standards';

/**
 * 평가기준 나누기 목. 실서버는 AI 로 나눌 예정이고, 목은 **줄 · 문장 단위**로 자릅니다.
 *
 * 결과 모양만 맞추면 됩니다 — FE 는 `standards` 를 목록으로, `except_standard` 를
 * 안내 문구로 그릴 뿐이라 나누는 품질은 화면 흐름 확인에 상관없습니다.
 */

/** 서버가 받아 주는 개수. FE 의 MAX_CRITERIA 와 같습니다 */
const MAX_STANDARDS = 5;

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

    const all = splitStandards(standard_text);
    const rest = all.slice(MAX_STANDARDS);
    const body: StandardTextResponse = {
      pitch_id: String(params.pitchId),
      standards: all.slice(0, MAX_STANDARDS).map((standard) => ({ standard })),
      except_standard: rest.length > 0 ? rest.join(' / ') : null,
    };
    return HttpResponse.json(body);
  }),
];
