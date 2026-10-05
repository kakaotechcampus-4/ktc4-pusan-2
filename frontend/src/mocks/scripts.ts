import { delay, http, HttpResponse } from 'msw';
import type {
  ParseRequested,
  ScriptCreateRequest,
  ScriptCreated,
  ScriptDetail,
  ScriptSlide,
} from '@/types/script';

/**
 * 대본 목. BE 처럼 올리면 바로 PENDING 으로 돌려주고, 잠시 뒤 폴링에서 DONE 이 됩니다.
 *
 * 나누는 규칙은 AI(script-parser v1)를 흉내 냅니다 — "슬라이드 1", "Slide 1", "1." 같은
 * **명시적 구분자**로만 나누고, 구분자가 없거나 앞부분에 구분 안 된 글이 있으면
 * 나누지 않고 전체를 한 슬라이드로 둡니다 (`segmented: false`, 정상 결과).
 */

/** 이만큼 지나야 DONE. "나누는 중" 화면을 눈으로 확인할 수 있게 */
const PARSE_MS = 1500;

const SEPARATOR = /^\s*(?:(?:slide|슬라이드)\s*(\d+)|(\d+)\s*[.)])\s*[:.\-–)]?\s*/i;

export function splitScript(text: string): { segmented: boolean; slides: ScriptSlide[] } {
  // AI 처럼 원문에 적힌 번호를 그대로 씁니다 — "슬라이드 4" 는 4번입니다
  const sections: { number: number; lines: string[] }[] = [];
  let lead = '';
  for (const line of text.split('\n')) {
    const m = SEPARATOR.exec(line);
    if (m) sections.push({ number: Number(m[1] ?? m[2]), lines: [line.replace(SEPARATOR, '')] });
    else if (sections.length > 0) sections.at(-1)!.lines.push(line);
    else lead += line;
  }

  const whole = (content: string): ScriptSlide => ({
    slide_number: 1,
    content: content.trim(),
    keywords: [],
    highlights: [],
  });

  if (sections.length === 0 || lead.trim() !== '') {
    return { segmented: false, slides: [whole(text)] };
  }
  return {
    segmented: true,
    slides: sections.map(({ number, lines }) => ({
      ...whole(lines.join('\n')),
      slide_number: number,
    })),
  };
}

interface Stored {
  pitchId: string;
  version: number;
  content: string;
  requestedAt: number;
}

const stored = new Map<string, Stored>();
const versions = new Map<string, number>();

function detail(id: string, s: Stored): ScriptDetail {
  const base: ScriptDetail = {
    script_version_id: id,
    version: s.version,
    original_content: s.content,
    parse_status: 'PENDING',
    segmented: null,
    slides: [],
    terms: [],
    error_code: null,
  };
  if (Date.now() - s.requestedAt < PARSE_MS) return base;
  const { segmented, slides } = splitScript(s.content);
  return { ...base, parse_status: 'DONE', segmented, slides };
}

export const scriptHandlers = [
  http.post('*/api/pitches/:pitchId/scripts', async ({ request, params }) => {
    const { content } = (await request.json()) as ScriptCreateRequest;
    if (!content.trim()) return HttpResponse.json({ code: 'INVALID_SCRIPT' }, { status: 400 });
    await delay(300);

    const pitchId = String(params.pitchId);
    const version = (versions.get(pitchId) ?? 0) + 1;
    versions.set(pitchId, version);
    const id = crypto.randomUUID();
    stored.set(id, { pitchId, version, content, requestedAt: Date.now() });

    const res: ScriptCreated = { script_version_id: id, version, parse_status: 'PENDING' };
    return HttpResponse.json(res, { status: 202 });
  }),

  http.get('*/api/pitches/:pitchId/scripts/:scriptVersionId', ({ params }) => {
    const id = String(params.scriptVersionId);
    const s = stored.get(id);
    if (!s) return HttpResponse.json({ code: 'SCRIPT_NOT_FOUND' }, { status: 404 });
    return HttpResponse.json(detail(id, s));
  }),

  http.post('*/api/pitches/:pitchId/scripts/:scriptVersionId/parse', ({ params }) => {
    const id = String(params.scriptVersionId);
    const s = stored.get(id);
    if (!s) return HttpResponse.json({ code: 'SCRIPT_NOT_FOUND' }, { status: 404 });
    s.requestedAt = Date.now();
    const res: ParseRequested = { script_version_id: id, parse_status: 'PENDING' };
    return HttpResponse.json(res, { status: 202 });
  }),
];
