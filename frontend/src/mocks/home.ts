import { http, HttpResponse } from 'msw';
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
  ],
};

export const homeHandlers = [http.get('*/api/pitches/', () => HttpResponse.json(homeFixture))];
