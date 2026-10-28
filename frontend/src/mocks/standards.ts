import { delay, http, HttpResponse } from 'msw';
import type {
  StandardTextRequest,
  StandardTextResponse,
  StandardsSaveRequest,
  StandardsSaved,
} from '@/types/standards';

/** 계약 값 — 표시 기준 한 줄 50자 · 10개까지 (`evaluation-criteria.md` 출력 표) */
const MAX_CHARS = 50;
const MAX_COUNT = 10;

/**
 * 고친 글을 "다시 분석"한 흉내. 실서버는 AI 로 다시 나누고 문장을 다듬어, 보낸 문장과 다를 수 있습니다.
 * 목은 공백을 줄이고 끝의 마침표를 뗍니다 — 화면이 돌려받은 목록으로 다시 그리는지 보려는 것입니다
 */
const reanalyze = (s: string) =>
  s
    .trim()
    .replace(/\s+/g, ' ')
    .replace(/[.。]$/, '');

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

    // AI 출력처럼 10개 · 한 줄 50자까지 냅니다. 목은 나누지 못한 문장을 따로 가리지 않습니다
    const body: StandardTextResponse = {
      pitch_id: String(params.pitchId),
      standards: splitStandards(standard_text)
        .slice(0, MAX_COUNT)
        .map((s) => ({ standard: s.slice(0, MAX_CHARS) })),
      except_standard: null,
    };
    return HttpResponse.json(body);
  }),

  // 고친 목록 저장 — 경로 · 모양은 FE 제안입니다(계약 304행 미정). 계약대로 다시 분석한 목록을 돌려줍니다
  http.post('*/api/pitches/:pitchId/standards', async ({ request }) => {
    const { standards } = (await request.json()) as StandardsSaveRequest;
    await delay(600);
    const invalid =
      standards.length === 0 ||
      standards.length > MAX_COUNT ||
      standards.some((s) => s.standard.trim() === '' || s.standard.length > MAX_CHARS);
    if (invalid) {
      return HttpResponse.json(
        {
          code: 'VALIDATION_ERROR',
          message: '평가기준은 1~10개, 한 줄 50자까지 저장할 수 있습니다.',
        },
        { status: 422 },
      );
    }
    const body: StandardsSaved = {
      standards: standards.map((s) => ({ standard: reanalyze(s.standard) })),
    };
    return HttpResponse.json(body);
  }),
];
