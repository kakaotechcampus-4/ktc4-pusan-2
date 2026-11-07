import { delay, http, HttpResponse } from 'msw';
import type { PitchListResponse } from '@/types/home';

/**
 * Updated #47 response including server-issued UUID fields.
 * Take 2 는 사용자가 끝내 end_reason 이 null, Take 3 은 완료됐지만 점수가 없고, 4 는 분석 중, 5 는 중단(FAILED + ABANDONED) 입니다.
 */
export const homeFixture: PitchListResponse = {
  pitches: [
    {
      pitch_id: '10000000-0000-4000-8000-000000000001',
      pitch_title: '캡스톤 최종 발표',
      pitch_time: 600,
      thumbnail_url: null,
      presentation_date: '2026-10-30',
      // Take 4 가 분석 중이라 지울 수 없습니다 (BE `can_delete`)
      can_delete: false,
      takes: [
        {
          take_id: '20000000-0000-4000-8000-000000000001',
          take_version: 1,
          take_elapsed: 620,
          take_time: 600,
          script_mode: 'FULL',
          score: 67,
          delta: null,
          status: 'COMPLETED',
          end_reason: 'SILENCE',
        },
        {
          take_id: '20000000-0000-4000-8000-000000000002',
          take_version: 2,
          take_elapsed: 582,
          take_time: 600,
          script_mode: 'HIGHLIGHT',
          score: 72,
          delta: 5,
          status: 'COMPLETED',
          end_reason: null,
        },
        {
          take_id: '20000000-0000-4000-8000-000000000003',
          take_version: 3,
          take_elapsed: 558,
          take_time: 600,
          script_mode: 'KEYWORD',
          score: null,
          delta: null,
          status: 'COMPLETED',
          end_reason: 'TIME_LIMIT',
        },
        {
          take_id: '20000000-0000-4000-8000-000000000004',
          take_version: 4,
          take_elapsed: 571,
          take_time: 600,
          script_mode: 'KEYWORD',
          score: null,
          delta: null,
          status: 'ANALYZING',
          end_reason: 'SILENCE',
        },
        {
          take_id: '20000000-0000-4000-8000-000000000005',
          take_version: 5,
          take_elapsed: null,
          take_time: 600,
          script_mode: 'HIGHLIGHT',
          score: null,
          delta: null,
          status: 'FAILED',
          end_reason: 'ABANDONED',
        },
      ],
    },
    // 삭제(#807)를 시험할 줄입니다. 위 피치는 `can_delete: false` 라 삭제가 막힙니다
    {
      pitch_id: '10000000-0000-4000-8000-000000000002',
      pitch_title: '창업 경진대회 본선',
      pitch_time: 420,
      thumbnail_url: null,
      presentation_date: null,
      can_delete: true,
      takes: [
        {
          take_id: '20000000-0000-4000-8000-000000000011',
          take_version: 1,
          take_elapsed: 431,
          take_time: 420,
          script_mode: 'HIGHLIGHT',
          score: 61,
          delta: null,
          status: 'COMPLETED',
          end_reason: null,
        },
      ],
    },
    {
      pitch_id: '10000000-0000-4000-8000-000000000003',
      pitch_title: '스터디 중간 공유',
      pitch_time: 300,
      thumbnail_url: null,
      presentation_date: null,
      can_delete: true,
      takes: [],
    },
  ],
};

/** 목에서 지운 피치. 새로고침하면 되살아납니다 */
const deleted = new Set<string>();
let failDelete = false;

declare global {
  interface Window {
    /** 개발 중 피치 삭제 실패 토글 (#807). 프로덕션 번들에는 들어가지 않습니다 */
    mockPitchDelete?: { fail: () => void; ok: () => void };
  }
}

if (import.meta.env.DEV && typeof window !== 'undefined') {
  // 콘솔에서 mockPitchDelete.fail() 뒤 삭제하면 500 으로 실패합니다. ok() 로 되돌립니다
  window.mockPitchDelete = {
    fail: () => {
      failDelete = true;
    },
    ok: () => {
      failDelete = false;
    },
  };
}

export const homeHandlers = [
  http.get('*/api/pitches/', () =>
    HttpResponse.json({
      pitches: homeFixture.pitches.filter((pitch) => !deleted.has(pitch.pitch_id)),
    } satisfies PitchListResponse),
  ),
  // BE 처럼 {message, pitch_id}. 없는 피치는 404 PITCH_NOT_FOUND, 지울 수 없는 피치는 409 PITCH_BUSY 입니다 (BE #1055)
  http.delete('*/api/pitches/delete/:pitchId', async ({ params }) => {
    const pitchId = String(params.pitchId);
    await delay(600);
    if (failDelete) return HttpResponse.json({ code: 'INTERNAL_ERROR' }, { status: 500 });
    const pitch = homeFixture.pitches.find((item) => item.pitch_id === pitchId);
    if (deleted.has(pitchId) || !pitch)
      return HttpResponse.json({ code: 'PITCH_NOT_FOUND' }, { status: 404 });
    if (!pitch.can_delete) return HttpResponse.json({ code: 'PITCH_BUSY' }, { status: 409 });
    deleted.add(pitchId);
    return HttpResponse.json({ message: 'Pitch deleted successfully', pitch_id: pitchId });
  }),
];
