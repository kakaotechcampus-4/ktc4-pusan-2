import { http, HttpResponse } from 'msw';
import type { PitchListResponse } from '@/types/home';

/** Updated #47 response including server-issued UUID fields. */
export const homeFixture: PitchListResponse = {
  pitches: [
    {
      pitch_id: '10000000-0000-4000-8000-000000000001',
      pitch_title: '캡스톤 최종 발표',
      pitch_time: 600,
      thumbnail_url: null,
      takes: [
        {
          take_id: '20000000-0000-4000-8000-000000000001',
          take_version: 1,
          take_elapsed: 620,
          take_time: 600,
          script_mode: 'FULL',
          score: 67,
          delta: null,
        },
        {
          take_id: '20000000-0000-4000-8000-000000000002',
          take_version: 2,
          take_elapsed: 582,
          take_time: 600,
          script_mode: 'HIGHLIGHT',
          score: 72,
          delta: 5,
        },
        {
          take_id: '20000000-0000-4000-8000-000000000003',
          take_version: 3,
          take_elapsed: 558,
          take_time: 600,
          script_mode: 'KEYWORD',
          score: null,
          delta: null,
        },
      ],
    },
  ],
};

export const homeHandlers = [http.get('*/api/pitches/', () => HttpResponse.json(homeFixture))];
