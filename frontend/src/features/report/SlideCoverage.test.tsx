// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { setupServer } from 'msw/node';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

// 시험 환경에는 앱 주소가 없어 상대 경로를 부를 수 없습니다 — `apiBase` 가 읽기 전에 정합니다
vi.hoisted(() => {
  import.meta.env.VITE_API_BASE = 'http://localhost';
});

import { reportHandlers } from '@/mocks/report';
import { useTakeReport } from '@/shared/api/report';
import { ContentCard } from './ContentCard';

/**
 * 장별 펼침(#1154 4절)을 목 BE(MSW) 위에서 실제로 눌러 봅니다 (BE 미구현 — #1153).
 * 리포트 훅까지 함께 써서, 409 뒤 리포트를 다시 읽고 새 판으로 장을 다시 부르는 흐름을 봅니다
 */
const server = setupServer(...reportHandlers);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

let host: HTMLDivElement;
let root: Root;

function Harness({ takeId }: { takeId: string }) {
  const report = useTakeReport(takeId);
  if (!report.data) return null;
  return <ContentCard takeId={takeId} status={report.data.content_status} />;
}

async function render(takeId: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Harness takeId={takeId} />
      </QueryClientProvider>,
    );
  });
  await until(() => host.textContent?.includes('내용 전달') ?? false);
}

/** 요청이 돌아와 화면이 바뀔 때까지 기다립니다 */
async function until(check: () => boolean, ms = 3000) {
  const end = Date.now() + ms;
  while (!check()) {
    if (Date.now() > end) throw new Error(`기다리다 끝남: ${host.textContent?.slice(0, 300)}`);
    await act(() => new Promise((r) => setTimeout(r, 20)));
  }
}

function rowButton(slide: number) {
  const button = [...host.querySelectorAll<HTMLButtonElement>('button[aria-expanded]')].find((b) =>
    b.textContent?.startsWith(`${slide}장`),
  );
  if (!button) throw new Error(`${slide}장 줄이 없습니다`);
  return button;
}

async function open(slide: number) {
  const button = rowButton(slide);
  expect(button.getAttribute('aria-expanded')).toBe('false');
  await act(async () => button.click());
  expect(rowButton(slide).getAttribute('aria-expanded')).toBe('true');
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  server.resetHandlers();
  vi.unstubAllGlobals();
});

describe('장별 펼침', () => {
  it('채점된 장 — 대본 문장 · 말한 문장의 대본 밖 구간 · 핵심 내용 · 수치', async () => {
    await render('t3');
    await open(3);
    await until(() => host.textContent?.includes('대본 문장') ?? false);
    const text = host.textContent ?? '';
    expect(text).toContain('전달바꿔 말함지난 분기 매출은 37% 늘었습니다.');
    expect(text).toContain('다르게 말함신규 고객 비중은 60%입니다.');
    expect(text).toContain('채점 안 함(다음 장으로 넘어갑니다)');

    // 대본 밖 구간은 <mark> 와 범주 이름 글자 — 이모지 뒤에서도 위치가 맞습니다
    const marks = [...host.querySelectorAll('mark')].map((m) => m.textContent);
    expect(marks).toEqual(['안녕하세요[진행 말]', '사실 저도 놀랐어요[대본 밖 말]']);

    expect(text).toContain('신규 고객 비중 60%중요다르게 말함');
    expect(text).toContain('60%40%다른 값');
    expect(text).toContain('3곳—확인 대기');
    expect(text).not.toMatch(/undefined|null|NaN/);
  });

  it('간이 장 — 실패 이유 · 간이 단계 · 수치 개수', async () => {
    await render('t3');
    await open(4);
    await until(() => host.textContent?.includes('수치 맞음') ?? false);
    const panel = host.textContent ?? '';
    expect(panel).toContain('채점이 시간 안에 끝나지 않았어요');
    expect(panel).toContain('간이절반쯤 말함(간이)');
    expect(panel).toContain('수치 맞음 1 · 어림 0 · 다른 값 0 · 빠뜨림 1 · 확인 대기 0');
  });

  it('간이 값도 없는 실패 장 — 이유만', async () => {
    await render('t-content-zero');
    await open(6);
    await until(() => !(host.textContent?.includes('불러오고 있어요') ?? true));
    const panel = host.querySelector('[id]')?.textContent ?? '';
    expect(panel).toBe('음성 인식이 끊겨 이 장을 평가하지 못했어요');
  });

  it('409 면 리포트를 다시 읽고 새 판으로 그 장을 다시 받습니다', async () => {
    await render('t-content-revised');
    await open(3);
    await until(() => host.textContent?.includes('대본 문장') ?? false);
    expect(rowButton(3).getAttribute('aria-expanded')).toBe('true');
  });

  it('다시 누르면 닫힙니다', async () => {
    await render('t3');
    await open(3);
    await act(async () => rowButton(3).click());
    expect(rowButton(3).getAttribute('aria-expanded')).toBe('false');
    expect(host.textContent).not.toContain('대본 문장');
  });
});
