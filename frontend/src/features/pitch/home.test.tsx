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

it('카드는 Take 목록 대신 Take 기록 · 비교 · 발표 준비 링크를 둔다', () => {
  const markup = renderToStaticMarkup(
    <MemoryRouter>
      <HomePitchCard pitch={homeFixture.pitches[0]} index={0} />
    </MemoryRouter>,
  );
  expect(markup).toContain('캡스톤 최종 발표');
  expect(markup).toContain('/pitch/10000000-0000-4000-8000-000000000001/edit');
  expect(markup).toContain('href="/takes?pitch=10000000-0000-4000-8000-000000000001"');
  expect(markup).toContain('/pitch/10000000-0000-4000-8000-000000000001/comparison');
  expect(markup).not.toContain('TAKE 01');
  expect(markup).not.toContain('연습 전');
});

it.each([0, 1, 2])('기록 %i개인 피치의 발표 준비는 제목과 같은 피치 준비 경로를 쓴다', (count) => {
  const markup = renderToStaticMarkup(
    <MemoryRouter>
      <HomePitchCard
        pitch={{
          ...homeFixture.pitches[0],
          pitch_id: 'pitch/id',
          takes: homeFixture.pitches[0].takes.slice(0, count),
        }}
        index={0}
      />
    </MemoryRouter>,
  );
  const links = [...markup.matchAll(/href="([^"]+)"/g)].map((match) => match[1]);
  expect(links[0]).toBe('/pitch/pitch%2Fid/edit');
  expect(links.at(-1)).toBe(links[0]);
  expect(markup).toContain('aria-label="캡스톤 최종 발표 발표 준비"');
  expect(markup).not.toContain('/device-check');
  expect(markup).not.toContain('/rehearsal');
});

it('피치 ID가 없으면 발표 준비 링크를 표시하지 않는다', () => {
  const markup = renderToStaticMarkup(
    <MemoryRouter>
      <HomePitchCard pitch={{ ...homeFixture.pitches[0], pitch_id: '' }} index={0} />
    </MemoryRouter>,
  );
  expect(markup).not.toContain('/edit');
  expect(markup).not.toContain('발표 준비');
});

it('연습 기록이 없는 피치는 연습 전으로 표시하고 비교 링크를 두지 않는다', () => {
  const markup = renderToStaticMarkup(
    <MemoryRouter>
      <HomePitchCard pitch={{ ...homeFixture.pitches[0], takes: [] }} index={0} />
    </MemoryRouter>,
  );
  expect(markup).toContain('연습 전');
  expect(markup).toContain('/pitch/10000000-0000-4000-8000-000000000001/edit');
  expect(markup).not.toContain('/comparison');
  expect(markup).not.toContain('href="/takes');
});

it('누적 발표 시간은 길이를 아는 Take 만 더하고, 하나도 모르면 0 이 아니라 — 로 둔다', () => {
  expect(totalElapsed([{ take_elapsed: 60 }, { take_elapsed: null }, { take_elapsed: 30 }])).toBe(
    90,
  );
  expect(duration(totalElapsed([{ take_elapsed: null }, { take_elapsed: null }]))).toBe('—');
  expect(duration(totalElapsed([]))).toBe('—');
});
