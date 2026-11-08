import { http, HttpResponse } from 'msw';
import { FIRST_TAKE_SLIDES, FIRST_TAKE_TITLE } from '@/features/pitch/tutorial/firstTakeMaterials';
import type { PrepareResponse } from '@/types/api';
import type { HomePitch } from '@/types/home';
import type { PitchRequest, PitchResources } from '@/types/pitch';

/** #931 개발 목: 기존 BE 계약만으로 First Take 생성부터 장치 점검까지 이어 줍니다. */
type Version = { id: string; version: number };
type PresentationVersion = Version & { pageCount: number };
type TutorialPitch = {
  id: string;
  title: string;
  timeLimitSec: number;
  presentations: PresentationVersion[];
  scripts: Version[];
};

let tutorial: TutorialPitch | null = null;

export function tutorialHomePitches(): HomePitch[] {
  if (!tutorial) return [];
  return [
    {
      pitch_id: tutorial.id,
      pitch_title: tutorial.title,
      pitch_time: tutorial.timeLimitSec,
      thumbnail_url: null,
      presentation_date: null,
      takes: [],
      can_delete: true,
    },
  ];
}

export function recordTutorialPresentation(
  pitchId: string,
  id: string,
  version: number,
  pageCount: number,
) {
  if (tutorial?.id !== pitchId) return;
  tutorial.presentations.push({ id, version, pageCount });
}

export function recordTutorialScript(pitchId: string, id: string, version: number) {
  if (tutorial?.id !== pitchId) return;
  tutorial.scripts.push({ id, version });
}

/** 테스트마다 동적 튜토리얼 자료만 초기화합니다. 일반 피치 목은 건드리지 않습니다. */
export function resetTutorialMock() {
  tutorial = null;
}

export const firstTakeHandlers = [
  // 일반 피치 생성은 기존 pitchHandlers로 넘깁니다.
  http.post('*/api/pitches/add', async ({ request }) => {
    const body = (await request.json()) as PitchRequest;
    if (body.title !== FIRST_TAKE_TITLE) return undefined;
    if (!tutorial) {
      tutorial = {
        id: crypto.randomUUID(),
        title: body.title,
        timeLimitSec: body.time_limit_sec,
        presentations: [],
        scripts: [],
      };
    }
    return HttpResponse.json({ message: 'Pitch added successfully', pitch_id: tutorial.id });
  }),

  http.get('*/api/pitches/:pitchId', ({ params }) => {
    if (tutorial?.id !== params.pitchId) return undefined;
    return HttpResponse.json({
      pitch_id: tutorial.id,
      title: tutorial.title,
      time_limit_sec: tutorial.timeLimitSec,
      lower_deviation: 30,
      upper_deviation: 60,
      presentation_date: null,
    });
  }),

  http.get('*/api/pitches/:pitchId/resources', ({ params }) => {
    if (tutorial?.id !== params.pitchId) return undefined;
    const body: PitchResources = {
      pitch_id: tutorial.id,
      presentation_versions: tutorial.presentations.map(({ id, version, pageCount }) => ({
        id,
        version,
        page_count: pageCount,
      })),
      script_versions: tutorial.scripts.map(({ id, version }) => ({ id, version })),
      evaluation_versions: [],
    };
    return HttpResponse.json(body);
  }),

  http.get('*/api/pitches/:pitchId/prepare', ({ params }) => {
    if (tutorial?.id !== params.pitchId) return undefined;
    const presentation = tutorial.presentations.at(-1);
    const script = tutorial.scripts.at(-1);
    const body: PrepareResponse = {
      pitch_id: tutorial.id,
      title: tutorial.title,
      time_limit_sec: tutorial.timeLimitSec,
      presentation: presentation
        ? { id: presentation.id, version: presentation.version, page_count: presentation.pageCount }
        : null,
      script: script
        ? { id: script.id, version: script.version, slide_count: FIRST_TAKE_SLIDES.length }
        : null,
      newer_script: null,
      next_take_number: 1,
      default_script_mode: 'HIGHLIGHT',
      missions: null,
    };
    return HttpResponse.json(body);
  }),

  http.delete('*/api/pitches/delete/:pitchId', ({ params }) => {
    if (tutorial?.id !== params.pitchId) return undefined;
    const pitchId = tutorial.id;
    tutorial = null;
    return HttpResponse.json({ message: 'Pitch deleted successfully', pitch_id: pitchId });
  }),
];
