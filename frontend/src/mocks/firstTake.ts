import { http, HttpResponse } from 'msw';
import { FIRST_TAKE_SLIDES } from '@/features/pitch/tutorial/firstTakeMaterials';
import type { PrepareResponse } from '@/types/api';
import type { HomePitch } from '@/types/home';
import type { FirstTakeResponse, PitchDetail, PitchResources } from '@/types/pitch';
import type { PresentationDetail } from '@/types/presentation';
import type { ScriptDetail } from '@/types/script';
import { samplePdfUrl } from './samplePdf';

/**
 * #931 개발 목: First Take 튜토리얼 샘플 피치 (BE #1135 `POST /pitches/tutorials/first-take`).
 *
 * BE 처럼 사용자마다 하나라 처음 부를 때 만들고 그 뒤로는 같은 피치를 줍니다. 대본 나누기가 끝난 상태로
 * 만들어 홈 목록 · 발표 준비 정보 · 자료 · 대본을 같은 피치 id 로 이어 줍니다. 새로고침하면 사라집니다.
 * 리포트를 이미 받은 계정은 콘솔에서 `mockFirstTake.used()` 로 흉내 냅니다.
 */
const TITLE = '[튜토리얼] 치치와 함께하는 First Take';
const PAGES = FIRST_TAKE_SLIDES.length;

type Tutorial = { pitchId: string; presentationId: string; scriptId: string };

let tutorial: Tutorial | null = null;
let reportReceived = false;

declare global {
  interface Window {
    /** 개발 중 튜토리얼 리포트 1회 제한 토글 (#931). 프로덕션 번들에는 들어가지 않습니다 */
    mockFirstTake?: { used: () => void; fresh: () => void };
  }
}

let reportAt: string | null = null;

/** 튜토리얼 리포트를 받은 계정인지 정합니다. 개발 토글과 테스트가 씁니다 */
export function setTutorialReportReceived(value: boolean) {
  reportReceived = value;
  reportAt = value ? new Date().toISOString() : null;
}

/** 내 정보(`GET /users/me`)의 `tutorial_report_at`. 목은 사용자가 한 명이라 여기 둡니다 */
export const tutorialReportAt = () => reportAt;

if (import.meta.env.DEV && typeof window !== 'undefined') {
  window.mockFirstTake = {
    used: () => setTutorialReportReceived(true),
    fresh: () => setTutorialReportReceived(false),
  };
}

/** 테스트마다 샘플 피치와 리포트 기록을 지웁니다. 일반 피치 목은 건드리지 않습니다 */
export function resetTutorialMock() {
  tutorial = null;
  setTutorialReportReceived(false);
}

/** 홈 목록에 붙일 샘플 피치. 아직 만들지 않았으면 없습니다 */
export function tutorialHomePitches(): HomePitch[] {
  if (!tutorial) return [];
  return [
    {
      pitch_id: tutorial.pitchId,
      pitch_title: TITLE,
      pitch_time: 180,
      thumbnail_url: null,
      presentation_date: null,
      takes: [],
      can_delete: true,
      is_demo: true,
    },
  ];
}

/** 이 목이 만든 샘플 피치가 아니면 null — 다른 목이 받게 넘깁니다 */
const mine = (pitchId: unknown) => (tutorial?.pitchId === pitchId ? tutorial : null);

const pdfUrls = new Map<string, string>();
const pdfOf = (id: string) => {
  const url = pdfUrls.get(id) ?? samplePdfUrl(PAGES, 'First Take');
  pdfUrls.set(id, url);
  return url;
};

export const firstTakeHandlers = [
  // BE 처럼 리포트를 받은 계정에 샘플 피치가 없으면 새로 만들지 않고 pitch_id 를 비웁니다
  http.post('*/api/pitches/tutorials/first-take', () => {
    if (!tutorial && !reportReceived)
      tutorial = {
        pitchId: crypto.randomUUID(),
        presentationId: crypto.randomUUID(),
        scriptId: crypto.randomUUID(),
      };
    return HttpResponse.json({
      pitch_id: tutorial?.pitchId ?? null,
      report_received: reportReceived,
    } satisfies FirstTakeResponse);
  }),

  http.get('*/api/pitches/:pitchId', ({ params }) => {
    const current = mine(params.pitchId);
    if (!current) return undefined;
    return HttpResponse.json({
      pitch_id: current.pitchId,
      title: TITLE,
      time_limit_sec: 180,
      lower_deviation: 30,
      upper_deviation: 60,
      presentation_date: null,
      is_demo: true,
    } satisfies PitchDetail);
  }),

  http.get('*/api/pitches/:pitchId/resources', ({ params }) => {
    const current = mine(params.pitchId);
    if (!current) return undefined;
    return HttpResponse.json({
      pitch_id: current.pitchId,
      presentation_versions: [{ id: current.presentationId, version: 1, page_count: PAGES }],
      script_versions: [{ id: current.scriptId, version: 1 }],
      evaluation_versions: [],
    } satisfies PitchResources);
  }),

  http.get('*/api/pitches/:pitchId/prepare', ({ params }) => {
    const current = mine(params.pitchId);
    if (!current) return undefined;
    return HttpResponse.json({
      pitch_id: current.pitchId,
      title: TITLE,
      time_limit_sec: 180,
      presentation: { id: current.presentationId, version: 1, page_count: PAGES },
      script: { id: current.scriptId, version: 1, slide_count: PAGES },
      newer_script: null,
      next_take_number: 1,
      default_script_mode: 'HIGHLIGHT',
      missions: null,
      tutorial_block: reportReceived ? 'TUTORIAL_REPORT_USED' : null,
    } satisfies PrepareResponse);
  }),

  http.get('*/api/pitches/:pitchId/presentations/:versionId', ({ params }) => {
    const current = mine(params.pitchId);
    if (!current || params.versionId !== current.presentationId) return undefined;
    return HttpResponse.json({
      pitch_id: current.pitchId,
      presentation_version_id: current.presentationId,
      version: 1,
      file_url: pdfOf(current.presentationId),
      page_count: PAGES,
      description: null,
      created_at: new Date().toISOString().slice(0, 10),
    } satisfies PresentationDetail);
  }),

  http.get('*/api/pitches/:pitchId/scripts/:scriptVersionId', ({ params }) => {
    const current = mine(params.pitchId);
    if (!current || params.scriptVersionId !== current.scriptId) return undefined;
    return HttpResponse.json({
      script_version_id: current.scriptId,
      version: 1,
      original_content: null,
      parse_status: 'DONE',
      segmented: true,
      slides: FIRST_TAKE_SLIDES.map((slide, index) => ({
        slide_number: index + 1,
        content: `${slide.title}. ${slide.subtitle}`,
        keywords: [],
        highlights: [],
      })),
      terms: [],
      error_code: null,
    } satisfies ScriptDetail);
  }),

  // BE 처럼 리포트를 받은 계정은 샘플 피치로 Take 를 만들지 못합니다. 아니면 기본 Take 목이 받습니다
  http.post('*/api/pitches/:pitchId/takes/', ({ params }) => {
    if (!mine(params.pitchId) || !reportReceived) return undefined;
    return HttpResponse.json(
      {
        code: 'TUTORIAL_REPORT_USED',
        message: '튜토리얼 리포트는 한 번만 받을 수 있어요.',
        detail: null,
      },
      { status: 409 },
    );
  }),

  http.delete('*/api/pitches/delete/:pitchId', ({ params }) => {
    const current = mine(params.pitchId);
    if (!current) return undefined;
    tutorial = null;
    return HttpResponse.json({ message: 'Pitch deleted successfully', pitch_id: current.pitchId });
  }),
];
