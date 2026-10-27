import { afterEach, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { fetchPitchList } from '@/shared/api/home';
import { HomePitchCard } from './HomePitchCard';
import { duration, totalElapsed } from './homeFormat';
import { homeFixture } from '@/mocks/home';
import { clearTokens } from '@/shared/api/tokenStore';

afterEach(() => {
  vi.unstubAllGlobals();
  clearTokens();
});

it('uses the authenticated client and the #47 trailing-slash endpoint', async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json(homeFixture));
  vi.stubGlobal('fetch', fetch);
  expect(await fetchPitchList()).toEqual(homeFixture);
  expect(fetch.mock.calls[0][0]).toMatch(/\/api\/pitches\/$/);
  expect(fetch.mock.calls[0][1]).toMatchObject({ credentials: 'include' });
});

it('accepts an empty pitch list without fabricating records', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ pitches: [] })));
  expect(await fetchPitchList()).toEqual({ pitches: [] });
});

it('keeps request errors as failures instead of showing an empty home', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(Response.json({ code: 'FAILED' }, { status: 500 })),
  );
  await expect(fetchPitchList()).rejects.toMatchObject({ status: 500 });
});

it('uses server UUIDs for editor and report URLs instead of version numbers', () => {
  const markup = renderToStaticMarkup(
    <MemoryRouter>
      <HomePitchCard pitch={homeFixture.pitches[0]} index={0} />
    </MemoryRouter>,
  );
  expect(markup).toContain('캡스톤 최종 발표');
  expect(markup).toContain('09:18');
  expect(markup).toContain('점수 미제공');
  expect(markup).toContain('/pitch/10000000-0000-4000-8000-000000000001/edit');
  for (const take of homeFixture.pitches[0].takes.filter((t) => t.status === 'COMPLETED'))
    expect(markup).toContain(`href="/takes/${take.take_id}"`);
  expect(markup).not.toContain('href="/takes/3"');
  expect(markup.indexOf('TAKE 03')).toBeLessThan(markup.indexOf('TAKE 01'));
  expect(markup).not.toContain('연습 전');
});

it('연습 기록이 없는 피치는 연습 전으로 표시하고 이어서 준비할 수 있다', () => {
  const markup = renderToStaticMarkup(
    <MemoryRouter>
      <HomePitchCard pitch={{ ...homeFixture.pitches[0], takes: [] }} index={0} />
    </MemoryRouter>,
  );
  expect(markup).toContain('연습 전');
  expect(markup).toContain('피치 제목을 눌러 자료와 대본을 이어서 준비');
  expect(markup).toContain('/pitch/10000000-0000-4000-8000-000000000001/edit');
});

it('점수가 아니라 상태로 배지와 이동을 정한다 — 분석 중은 진행 화면, 중단은 링크 없음', () => {
  const markup = renderToStaticMarkup(
    <MemoryRouter>
      <HomePitchCard pitch={homeFixture.pitches[0]} index={0} />
    </MemoryRouter>,
  );
  const [, , third, analyzing, abandoned] = homeFixture.pitches[0].takes;
  // 완료됐지만 점수가 없는 Take 도 리포트로 갑니다
  expect(markup).toContain(`href="/takes/${third.take_id}"`);
  expect(markup).toContain(`href="/takes/${analyzing.take_id}/processing"`);
  expect(markup).not.toContain(abandoned.take_id);
  for (const label of ['완료', '분석 중', '중단']) expect(markup).toContain(label);
});

it('preserves zero scores and deltas; enables navigation only for supplied IDs', () => {
  const pitch = {
    ...homeFixture.pitches[0],
    pitch_id: 'real-pitch',
    takes: [
      {
        ...homeFixture.pitches[0].takes[0],
        take_id: 'real-take',
        score: 0,
        delta: 0,
        take_elapsed: 0,
      },
    ],
  };
  const markup = renderToStaticMarkup(
    <MemoryRouter>
      <HomePitchCard pitch={pitch} index={0} />
    </MemoryRouter>,
  );
  expect(markup).toContain('0점');
  expect(markup).toContain('변화 없음');
  expect(markup).toContain('00:00');
  expect(markup).toContain('/pitch/real-pitch/edit');
  expect(markup).toContain('/takes/real-take');
});

it('누적 발표 시간은 길이를 아는 Take 만 더하고, 하나도 모르면 0 이 아니라 — 로 둔다', () => {
  expect(totalElapsed([{ take_elapsed: 60 }, { take_elapsed: null }, { take_elapsed: 30 }])).toBe(
    90,
  );
  expect(duration(totalElapsed([{ take_elapsed: null }, { take_elapsed: null }]))).toBe('—');
  expect(duration(totalElapsed([]))).toBe('—');
});
