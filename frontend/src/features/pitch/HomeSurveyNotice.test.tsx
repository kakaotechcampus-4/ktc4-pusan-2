// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { User } from '@/shared/api/useSession';
import { HomeSurveyNotice } from './HomeSurveyNotice';

const session = vi.hoisted(() => ({ user: null as User | null }));
vi.mock('@/shared/api/useSession', () => ({ useSession: () => ({ data: session.user }) }));
const request = vi.hoisted(() => vi.fn());
vi.mock('@/shared/api/client', () => ({ apiRequest: request }));

const base: User = {
  id: 'u1',
  email: 'demo@pitchcoach.local',
  name: '데모',
  raw_data_consent: false,
  test_consent_at: null,
  test_consent_version: null,
};

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  request.mockReset();
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

async function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () =>
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <HomeSurveyNotice />
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  );
  await flush();
}
// React Query 는 결과를 setTimeout 한 번 뒤에 알립니다
const flush = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
  });
const button = (text: string) =>
  [...host.querySelectorAll('button')].find((b) => b.textContent === text);

it('동의하지 않았으면 참여 안내를 두고, 동의하면 안내문 버전을 보낸다', async () => {
  session.user = base;
  request.mockResolvedValue({
    ...base,
    test_consent_at: '2026-10-30T00:00:00Z',
    test_consent_version: 1,
  });
  await render();
  expect(host.textContent).toContain('사용자 테스트에 참여해 주세요');
  await act(async () => button('안내 보고 참여하기')!.click());
  expect(host.querySelector('dialog')?.textContent).toContain('음성 · 영상 원본은');

  await act(async () => button('동의하고 참여하기')!.click());
  await flush();
  expect(request).toHaveBeenCalledWith('/api/users/me', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ test_consent_version: 1 }),
  });
  expect(host.querySelector('dialog')).toBeNull();
});

it('동의했으면 설문을 남기지 않은 리포트로 가는 링크를 보여 준다', async () => {
  session.user = { ...base, test_consent_at: '2026-10-30T00:00:00Z', test_consent_version: 1 };
  request.mockResolvedValue({
    items: [
      {
        take_id: 't1',
        pitch_id: 'p1',
        pitch_title: '캡스톤 최종 발표',
        take_number: 2,
        completed_at: '2026-10-30T01:00:00Z',
      },
    ],
  });
  await render();
  expect(request).toHaveBeenCalledWith('/api/take-surveys/pending');
  expect(host.textContent).toContain('설문을 기다리는 리포트가 1개 있어요');
  expect(host.querySelector('a')?.getAttribute('href')).toBe('/takes/t1');
});

it('남은 설문이 없으면 아무것도 두지 않는다', async () => {
  session.user = { ...base, test_consent_at: '2026-10-30T00:00:00Z', test_consent_version: 1 };
  request.mockResolvedValue({ items: [] });
  await render();
  expect(host.textContent).toBe('');
});
