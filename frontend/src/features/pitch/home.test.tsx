import { afterEach, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { fetchHome } from '@/shared/api/home';
import { HomePitchCard } from './HomePitchCard';
import { homeFixture } from '@/mocks/home';
import { clearTokens } from '@/shared/api/tokenStore';

afterEach(() => {
  vi.unstubAllGlobals();
  clearTokens();
});

it('uses the authenticated client and the #47 trailing-slash endpoint', async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json(homeFixture));
  vi.stubGlobal('fetch', fetch);
  expect(await fetchHome()).toEqual(homeFixture);
  expect(fetch.mock.calls[0][0]).toMatch(/\/api\/pitches\/$/);
  expect(fetch.mock.calls[0][1]).toMatchObject({ credentials: 'include' });
});

it('accepts an empty pitch list without fabricating records', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ pitches: [] })));
  expect(await fetchHome()).toEqual({ pitches: [] });
});

it('keeps request errors as failures instead of showing an empty home', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(Response.json({ code: 'FAILED' }, { status: 500 })),
  );
  await expect(fetchHome()).rejects.toMatchObject({ status: 500 });
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
  for (const take of homeFixture.pitches[0].takes)
    expect(markup).toContain('/takes/' + take.take_id);
  expect(markup).not.toContain('href="/takes/3"');
  expect(markup.indexOf('TAKE 03')).toBeLessThan(markup.indexOf('TAKE 01'));
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
