// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiFailure } from '@/shared/api/tokenStore';
import type { MemorySettings } from '@/types/chat';
import {
  CardList,
  MEMORY_CHANGED_TEXT,
  MEMORY_REMOVE_FAILED_TEXT,
  MEMORY_REMOVED_TEXT,
  type CardActions,
} from './ChatCards';
import { ChatMemorySettings } from './ChatMemorySettings';
import { MEMORY_TEXT } from './chatMemory';
import { emptyBasis, readCard } from './chatResponse';
import { useChatStore } from './chatStore';

/**
 * 기억 설정 화면 · '기억했어요' 카드 [지우기] (12-4 F14 · F16, L3-43 · L3-45).
 * 서버는 경로 · 방법마다 답을 바꿔 끼웁니다 — 요청은 `calls` 에 남습니다
 */
const server = vi.hoisted(() => ({
  calls: [] as string[],
  get: (): Promise<unknown> => Promise.resolve({ enabled: true, items: [] }),
  put: (_enabled: boolean): Promise<unknown> => Promise.resolve({ enabled: true, items: [] }),
  clear: (): Promise<unknown> => Promise.resolve({ enabled: true, items: [] }),
  remove: (_itemId: string): Promise<unknown> => Promise.resolve({ enabled: true, items: [] }),
}));
vi.mock('@/shared/api/client', async (original) => ({
  ...(await original<typeof import('@/shared/api/client')>()),
  apiRequest: (path: string, init: RequestInit = {}) => {
    const method = init.method ?? 'GET';
    server.calls.push(`${method} ${path}`);
    if (path === '/api/chat/memory' && method === 'GET') return server.get();
    if (path === '/api/chat/memory' && method === 'PUT')
      return server.put((JSON.parse(String(init.body)) as { enabled: boolean }).enabled);
    if (path === '/api/chat/memory/items' && method === 'DELETE') return server.clear();
    const item = /^\/api\/chat\/memory\/items\/([^/]+)$/.exec(path);
    if (item && method === 'DELETE') return server.remove(decodeURIComponent(item[1]!));
    return Promise.reject(new Error(`모르는 경로 ${method} ${path}`));
  },
}));

const I1 = '20000000-0000-4000-8000-000000000001';
const I2 = '20000000-0000-4000-8000-000000000002';
const settings = (enabled: boolean, ...items: MemorySettings['items']): MemorySettings => ({
  enabled,
  items,
});
const tone = (value = '짧게') => ({ item_id: I1, key: 'TONE' as const, value });
const focus = { item_id: I2, key: 'FOCUS_AREA' as const, value: '시선' };

function held<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

let host: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  server.calls = [];
  server.get = () => Promise.resolve(settings(true));
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  useChatStore.getState().signOut();
  useChatStore.getState().setUser('u1');
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  queryClient.clear();
  useChatStore.getState().signOut();
  vi.unstubAllGlobals();
});

const settle = () =>
  act(async () => {
    for (let i = 0; i < 4; i++) await new Promise((resolve) => setTimeout(resolve, 0));
  });

const render = async (node: ReactNode) => {
  await act(async () =>
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{node}</MemoryRouter>
      </QueryClientProvider>,
    ),
  );
  await settle();
};

const buttons = () => [...host.querySelectorAll<HTMLButtonElement>('button')];
const button = (text: string) => buttons().find((b) => b.textContent === text);
const alerts = () => [...host.querySelectorAll('[role="alert"]')].map((a) => a.textContent);
const status = () => host.querySelector('[role="status"]')?.textContent ?? '';
const click = (el: Element) => act(async () => (el as HTMLElement).click());
const removeCalls = () =>
  server.calls.filter((c) => c.startsWith('DELETE /api/chat/memory/items/'));

// ── F16 '기억했어요' 카드 ─────────────────────────────

const actions = (): CardActions => ({
  decide: () => Promise.resolve('decided'),
  undo: () => Promise.resolve('undone'),
  choose: () => Promise.resolve(true),
  canSend: true,
});
const memoryCard = (itemId: string, value: string) =>
  readCard({ type: 'MEMORY_SAVED', item_id: itemId, key: 'TONE', value }, emptyBasis());
const cards = (...shown: ReturnType<typeof memoryCard>[]) => (
  <CardList cards={shown} runId="r-1" expired={false} actions={actions()} />
);

describe("'기억했어요' 카드 [지우기] (F16 · L3-45)", () => {
  it('[지우기]는 그 item_id 로 지우고, 지운 카드는 버튼만 끄고 그대로 남는다 — 설정 캐시도 같은 응답으로 바뀐다', async () => {
    server.get = () => Promise.resolve(settings(true, tone(), focus));
    server.remove = () => Promise.resolve(settings(true, focus));
    await render(cards(memoryCard(I1, '짧게')));
    expect(host.textContent).toContain('기억했어요');

    await click(button('지우기')!);
    await settle();
    expect(removeCalls()).toEqual([`DELETE /api/chat/memory/items/${I1}`]);
    expect(button('지우기')).toBeUndefined();
    expect(host.textContent).toContain('답 말투 · 길이: 짧게');
    expect(host.textContent).toContain(MEMORY_REMOVED_TEXT);
    expect(queryClient.getQueryData(['chat', 'memory', 'u1'])).toEqual(settings(true, focus));
  });

  it('누르는 동안 카드는 모두 막히고 연타해도 한 번만 보낸다 — 다른 항목도 막아 응답이 엇갈리지 않게 한다', async () => {
    server.get = () => Promise.resolve(settings(true, tone(), focus));
    const pending = held<unknown>();
    server.remove = () => pending.promise;
    const other = readCard(
      { type: 'MEMORY_SAVED', item_id: I2, key: 'FOCUS_AREA', value: '시선' },
      emptyBasis(),
    );
    await render(cards(memoryCard(I1, '짧게'), memoryCard(I1, '짧게'), other));

    const first = buttons()[0]!;
    await act(async () => {
      first.click();
      first.click();
    });
    await click(first);
    expect(removeCalls()).toHaveLength(1);
    const [a, b, c] = buttons();
    expect(a!.getAttribute('aria-disabled')).toBe('true');
    expect(b!.getAttribute('aria-disabled')).toBe('true');
    expect(b!.textContent).toBe('지우는 중');
    expect(c!.getAttribute('aria-disabled')).toBe('true');
    expect(c!.textContent).toBe('지우기');
    await click(c!);
    expect(removeCalls()).toHaveLength(1);

    await act(async () => pending.resolve(settings(true, focus)));
    await settle();
    expect(host.querySelectorAll(`[data-memory-item="${I1}"] button`)).toHaveLength(0);
  });

  it('이미 지운 항목(404)은 지운 것으로 보고 목록을 다시 읽는다 — 실패 안내를 띄우지 않는다', async () => {
    server.get = () => Promise.resolve(settings(true, tone()));
    server.remove = () => Promise.reject(new ApiFailure(404, 'NOT_FOUND'));
    await render(cards(memoryCard(I1, '짧게')));
    server.get = () => Promise.resolve(settings(true));
    const reads = server.calls.length;

    await click(button('지우기')!);
    await settle();
    expect(alerts()).toEqual([]);
    expect(host.textContent).toContain(MEMORY_REMOVED_TEXT);
    expect(server.calls.slice(reads)).toContain('GET /api/chat/memory');
  });

  it('지우지 못하면 [지우기]를 그대로 두고 실패를 알린다 — 다시 누르면 다시 보낸다', async () => {
    server.get = () => Promise.resolve(settings(true, tone()));
    server.remove = () => Promise.reject(new Error('끊김'));
    await render(cards(memoryCard(I1, '짧게')));

    await click(button('지우기')!);
    await settle();
    expect(alerts()).toEqual([MEMORY_REMOVE_FAILED_TEXT]);
    expect(button('지우기')!.getAttribute('aria-disabled')).toBe('false');

    server.remove = () => Promise.resolve(settings(true));
    await click(button('지우기')!);
    await settle();
    expect(removeCalls()).toHaveLength(2);
    expect(alerts()).toEqual([]);
    expect(host.textContent).toContain(MEMORY_REMOVED_TEXT);
  });

  it('같은 종류를 다시 기억해 값이 바뀐 지난 카드는 [지우기]를 끈다 — 지금 값을 지우지 않게', async () => {
    // BE 는 같은 key 면 item_id 를 두고 값만 바꿉니다 (`upsert_profile_item`)
    server.get = () => Promise.resolve(settings(true, tone('자세히')));
    await render(cards(memoryCard(I1, '짧게'), memoryCard(I1, '자세히')));
    const [oldCard, newCard] = [...host.querySelectorAll(`[data-memory-item="${I1}"]`)];
    expect(oldCard!.querySelector('button')).toBeNull();
    expect(oldCard!.textContent).toContain(MEMORY_CHANGED_TEXT);
    expect(newCard!.querySelector('button')!.textContent).toBe('지우기');
  });

  it('카드가 다시 그려져도(목록 열고 닫기 · 지난 메시지) 30초 안에는 기억 목록을 다시 읽지 않는다', async () => {
    server.get = () => Promise.resolve(settings(true, tone()));
    await render(cards(memoryCard(I1, '짧게')));
    const reads = () => server.calls.filter((c) => c === 'GET /api/chat/memory').length;
    expect(reads()).toBe(1);
    await act(async () => root.render(<></>));
    await render(cards(memoryCard(I1, '짧게')));
    expect(reads()).toBe(1);
  });

  it('설정 화면에서 지운 항목은 카드에서도 지운 것으로 보인다 (L3-45 설정에서 지운 뒤 카드)', async () => {
    server.get = () => Promise.resolve(settings(true, tone()));
    await render(cards(memoryCard(I1, '짧게')));
    expect(button('지우기')).toBeDefined();
    await act(async () => queryClient.setQueryData(['chat', 'memory', 'u1'], settings(true)));
    await settle();
    expect(button('지우기')).toBeUndefined();
    expect(host.textContent).toContain(MEMORY_REMOVED_TEXT);
  });

  it('목록을 읽지 못했으면 [지우기]를 둔다 — 지운 항목이면 서버가 404 로 알려 준다', async () => {
    server.get = () => Promise.reject(new Error('끊김'));
    await render(cards(memoryCard(I1, '짧게')));
    expect(button('지우기')!.getAttribute('aria-disabled')).toBe('false');
  });
});

// ── F14 기억 설정 화면 ─────────────────────────────

describe('기억 설정 화면 (F14 · L3-43)', () => {
  const screen = (onBack = () => undefined) => <ChatMemorySettings userId="u1" onBack={onBack} />;
  const toggle = () => host.querySelector<HTMLButtonElement>('[role="switch"]')!;
  const rows = () => [...host.querySelectorAll('[data-memory-row]')];

  it('설명 고정 문구 · 켜짐 · 저장된 항목(종류 · 값)을 보이고, [돌아가기]를 누르면 알린다', async () => {
    server.get = () => Promise.resolve(settings(true, tone(), focus));
    const onBack = vi.fn();
    await render(screen(onBack));
    expect(host.textContent).toContain(
      "끄거나 '기억 없이' 대화를 골라도 그 대화의 기록 · 요약은 보관되고, 다른 대화의 맞춤 답에만 쓰지 않아요.",
    );
    expect(toggle().getAttribute('aria-checked')).toBe('true');
    expect(rows().map((r) => r.textContent)).toEqual([
      '답 말투 · 길이짧게지우기',
      '먼저 보고 싶은 영역시선지우기',
    ]);
    await click(button('돌아가기')!);
    expect(onBack).toHaveBeenCalledOnce();
  });

  it('끄기는 서버가 받은 뒤에만 꺼짐 · 성공을 보이고, 누르는 동안 모든 버튼을 막는다', async () => {
    server.get = () => Promise.resolve(settings(true, tone()));
    const pending = held<unknown>();
    server.put = () => pending.promise;
    await render(screen());

    await click(toggle());
    await click(toggle());
    expect(server.calls.filter((c) => c.startsWith('PUT'))).toHaveLength(1);
    expect(toggle().getAttribute('aria-checked')).toBe('true');
    expect(toggle().textContent).toContain('바꾸는 중');
    expect(status()).toBe('');
    for (const b of [toggle(), button('지우기')!, button('전체 지우기')!])
      expect(b.getAttribute('aria-disabled')).toBe('true');

    await act(async () => pending.resolve(settings(false, tone())));
    await settle();
    expect(toggle().getAttribute('aria-checked')).toBe('false');
    expect(status()).toBe(MEMORY_TEXT.enabledOff);
    expect(button('전체 지우기')!.getAttribute('aria-disabled')).toBe('false');
  });

  it('켜기 · 끄기가 실패하면 이전 상태를 두고 [다시 시도]로 같은 값을 다시 보낸다', async () => {
    server.get = () => Promise.resolve(settings(true));
    const sent: boolean[] = [];
    server.put = (enabled) => {
      sent.push(enabled);
      return Promise.reject(new Error('끊김'));
    };
    await render(screen());
    await click(toggle());
    await settle();
    expect(toggle().getAttribute('aria-checked')).toBe('true');
    expect(alerts()).toEqual([`${MEMORY_TEXT.enabledFailed}다시 시도`]);
    expect(status()).toBe('');

    server.put = (enabled) => {
      sent.push(enabled);
      return Promise.resolve(settings(enabled));
    };
    await click(button('다시 시도')!);
    await settle();
    expect(sent).toEqual([false, false]);
    expect(toggle().getAttribute('aria-checked')).toBe('false');
    expect(alerts()).toEqual([]);
  });

  it('항목 [지우기]는 그 줄을 지우고 알린다 — 실패하면 줄을 두고 [다시 시도]', async () => {
    server.get = () => Promise.resolve(settings(true, tone(), focus));
    server.remove = () => Promise.reject(new Error('끊김'));
    await render(screen());
    await click(host.querySelector(`[data-memory-row="${I1}"] button`)!);
    await settle();
    expect(rows()).toHaveLength(2);
    expect(alerts()).toEqual([`${MEMORY_TEXT.clearFailed}다시 시도`]);

    server.remove = (itemId) =>
      Promise.resolve(itemId === I1 ? settings(true, focus) : settings(true, tone()));
    await click(button('다시 시도')!);
    await settle();
    expect(rows().map((r) => r.getAttribute('data-memory-row'))).toEqual([I2]);
    expect(status()).toBe(MEMORY_TEXT.removed);
  });

  it('[전체 지우기]는 서버가 지운 뒤 빈 목록과 성공을 보인다 — 실패하면 목록을 두고 [다시 시도]', async () => {
    server.get = () => Promise.resolve(settings(true, tone(), focus));
    server.clear = () => Promise.reject(new Error('끊김'));
    await render(screen());
    await click(button('전체 지우기')!);
    await click(button('모두 지우기')!);
    await settle();
    expect(rows()).toHaveLength(2);
    expect(alerts()).toEqual([`${MEMORY_TEXT.clearFailed}다시 시도`]);

    server.clear = () => Promise.resolve(settings(true));
    await click(button('다시 시도')!);
    await settle();
    expect(rows()).toHaveLength(0);
    expect(host.textContent).toContain(MEMORY_TEXT.empty);
    expect(status()).toBe(MEMORY_TEXT.cleared);
    expect(button('전체 지우기')!.getAttribute('aria-disabled')).toBe('true');
  });

  it('[전체 지우기]는 같은 자리에서 한 번 더 묻고 초점을 [취소]에 둔다 — [취소] · Esc 는 보내지 않고 돌아온다', async () => {
    server.get = () => Promise.resolve(settings(true, tone(), focus));
    // 대화창 자리 — Esc 가 여기까지 오면 대화창이 닫힙니다
    const outer = vi.fn();
    await render(<div onKeyDown={outer}>{screen()}</div>);
    const clears = () => server.calls.filter((c) => c === 'DELETE /api/chat/memory/items');

    await click(button('전체 지우기')!);
    expect(host.textContent).toContain(MEMORY_TEXT.clearConfirm);
    expect(document.activeElement).toBe(button('취소'));
    await click(button('취소')!);
    expect(button('모두 지우기')).toBeUndefined();
    expect(document.activeElement).toBe(button('전체 지우기'));

    await click(button('전체 지우기')!);
    const esc = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    await act(async () => document.activeElement!.dispatchEvent(esc));
    expect(outer).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(button('전체 지우기'));
    expect(clears()).toHaveLength(0);

    await click(button('전체 지우기')!);
    await click(button('모두 지우기')!);
    await settle();
    expect(clears()).toHaveLength(1);
    expect(document.activeElement).toBe(button('전체 지우기'));
  });

  it('설정을 읽지 못하면 [다시 시도]로 다시 읽는다', async () => {
    server.get = () => Promise.reject(new Error('끊김'));
    await render(screen());
    expect(alerts()).toEqual([`${MEMORY_TEXT.loadFailed}다시 시도`]);
    server.get = () => Promise.resolve(settings(false));
    await click(button('다시 시도')!);
    await settle();
    expect(toggle().getAttribute('aria-checked')).toBe('false');
  });

  it('다시 열면 서버 값을 다시 읽어 다른 탭에서 바뀐 것을 맞춘다', async () => {
    server.get = () => Promise.resolve(settings(true, tone()));
    await render(screen());
    expect(rows()).toHaveLength(1);
    await act(async () => root.render(<></>));
    server.get = () => Promise.resolve(settings(false));
    await render(screen());
    expect(rows()).toHaveLength(0);
    expect(toggle().getAttribute('aria-checked')).toBe('false');
  });
});
