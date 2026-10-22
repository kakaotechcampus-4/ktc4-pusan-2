import { http, HttpResponse } from 'msw';
import type { PitchResources } from '@/types/pitch';
import type { PresentationDetail } from '@/types/presentation';
import type { ScriptDetail } from '@/types/script';
import type { EvaluationDetail } from '@/types/standards';
import { homeFixture } from './home';
import { samplePdfUrl } from './samplePdf';

/**
 * 수정 화면 목 — 홈 목록의 첫 피치("캡스톤 최종 발표")를 서버에 저장된 피치처럼 둡니다.
 * BE 응답 모양 그대로입니다 (`VersionSummaryDTO` · `PresentationDetailDTO` · `ScriptDetailDTO` · 평가기준 상세).
 *
 *   슬라이드  V1 3장 · V2 4장
 *   대본      V1 3장(슬라이드 V1 에 맞음) · V2 4장(슬라이드 V2 에 맞음)
 *   평가기준  V1 2개 · V2 3개
 */
const PITCH_ID = homeFixture.pitches[0]!.pitch_id;

const slides = [
  { id: 'edit-pv1', version: 1, pages: 3 },
  { id: 'edit-pv2', version: 2, pages: 4 },
];

const scriptOf = (id: string, version: number, paragraphs: string[]): ScriptDetail => ({
  script_version_id: id,
  version,
  original_content: paragraphs.map((p, i) => `슬라이드 ${i + 1}\n${p}`).join('\n\n'),
  parse_status: 'DONE',
  segmented: true,
  slides: paragraphs.map((content, i) => ({
    slide_number: i + 1,
    content,
    keywords: [],
    highlights: [],
  })),
  terms: [],
  error_code: null,
});

const scripts = [
  scriptOf('edit-sv1', 1, [
    '안녕하세요. 피치코치를 소개합니다.',
    '혼자 하는 발표 연습은 어디가 문제인지 알기 어렵습니다.',
    '감사합니다.',
  ]),
  scriptOf('edit-sv2', 2, [
    '안녕하세요. 발표 연습을 돕는 피치코치입니다.',
    '혼자 하는 연습은 말 속도와 시선을 스스로 알기 어렵습니다.',
    '피치코치는 연습마다 고칠 것 하나를 알려 줍니다.',
    '감사합니다.',
  ]),
];

const evaluations: EvaluationDetail[] = [
  {
    pitch_id: PITCH_ID,
    evaluation_version: 1,
    evaluations: [
      { order: 1, standard: '추임새 5번 이하' },
      { order: 2, standard: '청중을 자주 보기' },
    ],
  },
  {
    pitch_id: PITCH_ID,
    evaluation_version: 2,
    evaluations: [
      { order: 2, standard: '청중을 자주 보기' },
      { order: 1, standard: '추임새 3번 이하' },
      { order: 3, standard: '마지막에 목표 금액을 분명하게 말하기' },
    ],
  },
];

/** blob URL 은 페이지에서만 만들 수 있어 처음 부를 때 만듭니다 */
const pdfUrls = new Map<string, string>();
const pdfOf = (id: string, pages: number) => {
  const url = pdfUrls.get(id) ?? samplePdfUrl(pages, id);
  pdfUrls.set(id, url);
  return url;
};

const notFound = () => HttpResponse.json({ code: 'PITCH_NOT_FOUND' }, { status: 404 });

/** ★ 업로드 · 대본 목보다 먼저 와야 합니다 — 그쪽은 모르는 id 에 404 를 돌려줍니다 */
export const pitchEditHandlers = [
  http.get('*/api/pitches/:pitchId/resources', ({ params }) => {
    if (params.pitchId !== PITCH_ID) return notFound();
    const body: PitchResources = {
      pitch_id: PITCH_ID,
      presentation_versions: slides.map(({ id, version }) => ({ id, version })),
      script_versions: scripts.map((s) => ({ id: s.script_version_id, version: s.version })),
      evaluation_versions: evaluations.map((e) => ({
        id: `edit-ev${e.evaluation_version}`,
        version: e.evaluation_version,
      })),
    };
    return HttpResponse.json(body);
  }),

  http.get('*/api/pitches/:pitchId/presentations/:versionId', ({ params }) => {
    const slide = slides.find((s) => s.id === params.versionId);
    // 모르는 버전은 업로드 목이 받습니다
    if (!slide) return undefined;
    const body: PresentationDetail = {
      pitch_id: String(params.pitchId),
      presentation_version_id: slide.id,
      version: slide.version,
      file_url: pdfOf(slide.id, slide.pages),
      description: null,
      created_at: new Date().toISOString().slice(0, 10),
    };
    return HttpResponse.json(body);
  }),

  http.get('*/api/pitches/:pitchId/scripts/:scriptVersionId', ({ params }) => {
    const script = scripts.find((s) => s.script_version_id === params.scriptVersionId);
    if (!script) return undefined;
    return HttpResponse.json(script);
  }),

  http.get('*/api/pitches/:pitchId/evaluations/:version', ({ params }) => {
    const found = evaluations.find((e) => String(e.evaluation_version) === params.version);
    if (params.pitchId !== PITCH_ID || !found) return notFound();
    return HttpResponse.json(found);
  }),
];
