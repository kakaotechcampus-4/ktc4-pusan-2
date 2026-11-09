// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiFailure } from '@/shared/api/tokenStore';
import { pitchListKey } from '@/shared/api/home';
import { prepareKey } from '@/shared/api/prepare';
import { takeSurveyKeys } from '@/shared/api/takeSurvey';
import type { HomePitch, HomeTake, PitchListResponse } from '@/types/home';
import type { TakeStatus } from '@/types/api';
import { HomePage } from './HomePage';

vi.mock('@/shared/api/useSession', () => ({ useSession: () => ({ data: null }) }));
const request = vi.hoisted(() => vi.fn());
vi.mock('@/shared/api/client', () => ({ apiRequest: request }));

const take = (id: string, status: TakeStatus): HomeTake => ({
  take_id: id,
  take_version: 1,
  take_elapsed: 300,
  take_time: 300,
  script_mode: 'HIGHLIGHT',
  score: null,
  delta: null,
  status,
  end_reason: null,
});
const pitch = (
  id: string,
  title: string,
  takes: HomeTake[] = [],
  can_delete = true,
): HomePitch => ({
  pitch_id: id,
  pitch_title: title,
  pitch_time: 300,
  thumbnail_url: null,
  presentation_date: null,
  takes,
  can_delete,
});

let list: PitchListResponse;
/** DELETE 응답. 기본은 성공입니다 */
let onDelete: (id: string) => Promise<unknown>;
let host: HTMLDivElement;
let root: Root;
let client: QueryClient;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  list = {
    pitches: [
      pitch('p1', '캡스톤 최종 발표', [take('t1', 'COMPLETED'), take('t2', 'COMPLETED')]),
      pitch('p2', '창업 경진대회 본선'),
      pitch('p3', '분석 중인 발표', [take('t3', 'ANALYZING')], false),
    ],
  };
  // BE 처럼 can_delete 가 false 면 409 PITCH_BUSY 로 거절합니다
  onDelete = async (id) => {
    if (list.pitches.find((p) => p.pitch_id === id)?.can_delete === false)
      throw new ApiFailure(409, 'PITCH_BUSY');
    list = { pitches: list.pitches.filter((p) => p.pitch_id !== id) };
    return { message: 'Pitch deleted successfully', pitch_id: id };
  };
  request.mockReset();
  request.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path === '/api/pitches/') return structuredClone(list);
    if (options?.method === 'DELETE') return onDelete(path.split('/').at(-1)!);
    throw new ApiFailure(404, 'NOT_FOUND');
  });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function render() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () =>
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <HomePage />
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  );
  await flush();
}
const flush = () =>
  act(async () => {
    for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0));
  });
const more = (title: string) =>
  host.querySelector<HTMLButtonElement>(`button[aria-label="${title} 더보기"]`)!;
const menuItem = () => host.querySelector<HTMLButtonElement>('[role="menuitem"]');
const dialog = () => host.querySelector('dialog');
const dialogButton = (text: string) =>
  [...(dialog()?.querySelectorAll('button') ?? [])].find((b) => b.textContent === text)!;
const key = (el: Element, k: string) =>
  act(async () => {
    el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
  });
const click = (el: HTMLElement) =>
  act(async () => {
    el.click();
  });
const titles = () =>
  [...host.querySelectorAll('ul > li a[href$="/edit"]:first-child')].map(
    (a) => a.querySelector('.truncate')?.textContent,
  );
const deletes = () => request.mock.calls.filter(([, o]) => o?.method === 'DELETE');
const isList = ([path, o]: unknown[]) =>
  path === '/api/pitches/' && (o as RequestInit | undefined)?.method !== 'DELETE';

async function openDialog(title: string) {
  await click(more(title));
  await click(menuItem()!);
}

it('··· 는 메뉴 버튼이고, ↓ 로 열면 첫 항목에 초점이 가고 Esc 로 닫으면 ··· 로 돌아온다', async () => {
  await render();
  const button = more('창업 경진대회 본선');
  expect(button.getAttribute('aria-haspopup')).toBe('menu');
  expect(button.getAttribute('aria-expanded')).toBe('false');

  button.focus();
  await key(button, 'ArrowDown');
  expect(button.getAttribute('aria-expanded')).toBe('true');
  const menu = host.querySelector('[role="menu"]')!;
  expect(button.getAttribute('aria-controls')).toBe(menu.id);
  expect(document.activeElement).toBe(menuItem());
  expect(menuItem()!.textContent).toBe('삭제');

  // 항목이 하나라 ↑ ↓ 는 제자리입니다
  await key(menuItem()!, 'ArrowUp');
  expect(document.activeElement).toBe(menuItem());

  await key(menuItem()!, 'Escape');
  expect(menuItem()).toBeNull();
  expect(button.getAttribute('aria-expanded')).toBe('false');
  expect(document.activeElement).toBe(button);
});

it('Enter · Space(누르기)로 열면 첫 항목에 초점이 가고, 다시 누르면 닫는다', async () => {
  await render();
  const button = more('캡스톤 최종 발표');
  await click(button);
  expect(document.activeElement).toBe(menuItem());
  await click(button);
  expect(menuItem()).toBeNull();
});

it('BE 가 지울 수 없다고(can_delete: false) 하면 삭제를 aria-disabled 로 막고 이유를 보인다', async () => {
  await render();
  await click(more('분석 중인 발표'));
  const item = menuItem()!;
  expect(item.getAttribute('aria-disabled')).toBe('true');
  const reason = document.getElementById(item.getAttribute('aria-describedby')!)!;
  expect(reason.textContent).toBe('연습이나 분석이 끝난 뒤 지울 수 있어요.');
  await click(item);
  expect(dialog()).toBeNull();
  expect(menuItem()).not.toBeNull();
});

it('분석 중(ANALYZING)이어도 BE 가 지울 수 있다고(can_delete: true, 보류된 분석) 하면 지운다', async () => {
  list = { pitches: [pitch('p1', '보류된 발표', [take('t1', 'ANALYZING')], true)] };
  await render();
  await click(more('보류된 발표'));
  expect(menuItem()!.getAttribute('aria-disabled')).not.toBe('true');
  await click(menuItem()!);
  await click(dialogButton('삭제'));
  await flush();
  expect(deletes()).toHaveLength(1);
  expect(dialog()).toBeNull();
  expect(titles()).not.toContain('보류된 발표');
});

it('can_delete 가 없으면(BE 가 아직 안 내림) 막지 않고 BE 에 맡긴다', async () => {
  const { can_delete: _, ...old } = pitch('p1', '이전 응답', [take('t1', 'RUNNING')]);
  list = { pitches: [old as HomePitch] };
  await render();
  await click(more('이전 응답'));
  expect(menuItem()!.getAttribute('aria-disabled')).not.toBe('true');
});

it('확인 창은 제목 · 되돌릴 수 없음 · 함께 지워지는 것을 알리고, 취소에 처음 초점을 둔다. 취소하면 ··· 로 돌아온다', async () => {
  await render();
  await openDialog('캡스톤 최종 발표');
  const text = dialog()!.textContent;
  expect(text).toContain('캡스톤 최종 발표');
  expect(text).toContain('되돌릴 수 없어요');
  expect(text).toContain('Take 기록 2개와 리포트');
  // 설문 답은 지워지지 않아서(BE SET NULL) 따로 알립니다
  expect(text).toContain('설문을 남기셨다면 그 답은 지워지지 않고');
  expect(text).toContain('2026년 12월 31일까지 따로 보관돼요');
  expect(document.activeElement).toBe(dialogButton('취소'));

  await click(dialogButton('취소'));
  expect(dialog()).toBeNull();
  expect(document.activeElement).toBe(more('캡스톤 최종 발표'));
  expect(deletes()).toHaveLength(0);
});

it('Take 가 없는 피치는 남을 설문이 없어 보관 안내를 빼고 지워지는 것만 알린다', async () => {
  await render();
  await openDialog('창업 경진대회 본선');
  const text = dialog()!.textContent;
  expect(text).toContain('발표 자료 · 대본 · 평가 기준은 모두 함께 지워져요');
  expect(text).not.toContain('설문');
});

it('지우면 목록에서 빼고 관련 캐시를 지운 뒤, 같은 자리의 다음 줄로 초점을 옮긴다', async () => {
  await render();
  client.setQueryData(prepareKey('p1'), { title: 'old' });
  client.setQueryData(['takes', 't1', 'report'], { old: true });
  client.setQueryData(takeSurveyKeys.state('t2'), { old: true });
  client.setQueryData(['prepare', 'p2'], { keep: true });

  await openDialog('캡스톤 최종 발표');
  const before = request.mock.calls.length;
  // 두 번 빠르게 눌러도 요청은 한 번입니다
  await act(async () => {
    dialogButton('삭제').click();
    dialogButton('삭제').click();
  });
  await flush();

  expect(deletes()).toHaveLength(1);
  expect(deletes()[0][0]).toBe('/api/pitches/delete/p1');
  // 지우기 직전에 목록을 다시 받지 않고 DELETE 를 바로 보냅니다 (#1057)
  expect(request.mock.calls[before]![1]).toMatchObject({ method: 'DELETE' });
  expect(dialog()).toBeNull();
  expect(titles()).toEqual(['창업 경진대회 본선', '분석 중인 발표']);
  expect(client.getQueryData<PitchListResponse>(pitchListKey)?.pitches).toHaveLength(2);
  expect(client.getQueryData(prepareKey('p1'))).toBeUndefined();
  expect(client.getQueryData(['takes', 't1', 'report'])).toBeUndefined();
  expect(client.getQueryData(takeSurveyKeys.state('t2'))).toBeUndefined();
  expect(client.getQueryData(['prepare', 'p2'])).toEqual({ keep: true });
  expect(document.activeElement?.textContent).toContain('창업 경진대회 본선');
  expect(host.querySelector('[role="status"].sr-only')!.textContent).toBe(
    '‘캡스톤 최종 발표’ 피치를 삭제했어요.',
  );
});

it('창이 닫힐 때 목록이 아직 지운 줄을 담고 있어도, 지운 줄이 아닌 줄로 초점을 옮긴다', async () => {
  await render();
  // 목록 알림(setQueryData)이 창 닫기보다 늦게 그려지는 순서를 만듭니다 — 지운 줄이 그대로 남습니다
  vi.spyOn(client, 'setQueryData').mockImplementation(() => undefined);
  onDelete = async (id) => ({ message: 'Pitch deleted successfully', pitch_id: id });

  await openDialog('캡스톤 최종 발표');
  await click(dialogButton('삭제'));
  await flush();

  expect(dialog()).toBeNull();
  expect(titles()).toContain('캡스톤 최종 발표');
  expect(document.activeElement?.textContent).toContain('창업 경진대회 본선');
});

it('마지막 줄을 지우는데 그 줄이 아직 남아 있어도, 자기 자신이 아니라 윗줄로 옮긴다', async () => {
  list = { pitches: [pitch('p1', '하나'), pitch('p2', '둘')] };
  await render();
  vi.spyOn(client, 'setQueryData').mockImplementation(() => undefined);
  onDelete = async (id) => ({ message: 'Pitch deleted successfully', pitch_id: id });

  await openDialog('둘');
  await click(dialogButton('삭제'));
  await flush();

  expect(titles()).toContain('둘');
  expect(document.activeElement?.textContent).toContain('하나');
});

it('마지막 줄을 지우면 윗줄로, 줄이 다 사라지면 제목으로 초점을 옮긴다', async () => {
  list = { pitches: [pitch('p1', '하나'), pitch('p2', '둘')] };
  await render();
  await openDialog('둘');
  await click(dialogButton('삭제'));
  await flush();
  expect(document.activeElement?.textContent).toContain('하나');

  await openDialog('하나');
  await click(dialogButton('삭제'));
  await flush();
  expect(document.activeElement?.tagName).toBe('H1');
});

it('처리 중에는 두 버튼을 aria-disabled 로 막고, 실패하면 창과 줄을 그대로 두고 알린다', async () => {
  let fail: (e: unknown) => void = () => undefined;
  onDelete = () => new Promise((_, reject) => (fail = reject));
  await render();
  await openDialog('창업 경진대회 본선');
  await click(dialogButton('삭제'));
  await flush();

  const busy = dialogButton('삭제하는 중…');
  expect(busy.getAttribute('aria-disabled')).toBe('true');
  expect(dialogButton('취소').getAttribute('aria-disabled')).toBe('true');
  // 처리 중에는 취소 · Esc 가 듣지 않습니다
  await click(dialogButton('취소'));
  await act(async () => {
    dialog()!.dispatchEvent(new Event('cancel', { cancelable: true }));
  });
  expect(dialog()).not.toBeNull();
  // 막힌 버튼은 초점을 쥔 채로 둡니다
  busy.focus();

  await act(async () => fail(new ApiFailure(500, 'INTERNAL_ERROR')));
  await flush();
  expect(dialog()!.querySelector('[role="alert"]')!.textContent).toContain('삭제하지 못했어요');
  expect(dialogButton('삭제').getAttribute('aria-disabled')).toBe('false');
  expect(document.activeElement).toBe(dialogButton('삭제'));
  expect(titles()).toContain('창업 경진대회 본선');
});

it('실패 문구에 원인별 공통 오류 문구를 붙인다 — 로그인이 끝났으면 다시 시도하라고 하지 않는다', async () => {
  onDelete = async () => {
    throw new ApiFailure(401, 'REFRESH_FAILED');
  };
  await render();
  await openDialog('창업 경진대회 본선');
  await click(dialogButton('삭제'));
  await flush();
  expect(dialog()!.querySelector('[role="alert"]')!.textContent).toBe(
    '피치를 삭제하지 못했어요. 로그인이 만료됐어요. 다시 로그인해 주세요.',
  );
});

it('창을 연 사이 연습이 시작돼 BE 가 409 PITCH_BUSY 로 거절하면 알리고, 캐시는 두고 목록을 다시 받아 메뉴를 막는다', async () => {
  await render();
  client.setQueryData(prepareKey('p2'), { keep: true });
  await openDialog('창업 경진대회 본선');
  // 다른 창에서 연습을 시작했습니다
  list.pitches[1] = pitch('p2', '창업 경진대회 본선', [take('t9', 'RUNNING')], false);
  const before = request.mock.calls.length;
  await click(dialogButton('삭제'));
  await flush();

  const after = request.mock.calls.slice(before);
  expect(after[0]![1]).toMatchObject({ method: 'DELETE' });
  expect(after.slice(1).some(isList)).toBe(true);
  expect(dialog()!.querySelector('[role="alert"]')!.textContent).toBe(
    '지금 이 피치로 연습하거나 분석하는 중이에요. 끝난 뒤 다시 삭제해 주세요.',
  );
  expect(titles()).toContain('창업 경진대회 본선');
  expect(client.getQueryData(prepareKey('p2'))).toEqual({ keep: true });
  expect(
    client.getQueryData<PitchListResponse>(pitchListKey)?.pitches.find((p) => p.pitch_id === 'p2')
      ?.can_delete,
  ).toBe(false);

  await click(dialogButton('취소'));
  await click(more('창업 경진대회 본선'));
  expect(menuItem()!.getAttribute('aria-disabled')).toBe('true');
});

it('서버가 이미 없다고(404) 하면 지운 것으로 보고, 확인 창이 알던 Take 의 캐시를 지운다', async () => {
  // 다른 창에서 먼저 지웠습니다
  onDelete = async (id) => {
    list = { pitches: list.pitches.filter((p) => p.pitch_id !== id) };
    throw new ApiFailure(404, 'PITCH_NOT_FOUND');
  };
  await render();
  await openDialog('캡스톤 최종 발표');
  client.setQueryData(['takes', 't1', 'report'], { old: true });
  client.setQueryData(takeSurveyKeys.state('t2'), { old: true });
  client.setQueryData(['takes', 't3', 'report'], { keep: true });
  await click(dialogButton('삭제'));
  await flush();
  expect(dialog()).toBeNull();
  expect(titles()).not.toContain('캡스톤 최종 발표');
  expect(client.getQueryData(['takes', 't1', 'report'])).toBeUndefined();
  expect(client.getQueryData(takeSurveyKeys.state('t2'))).toBeUndefined();
  expect(client.getQueryData(['takes', 't3', 'report'])).toEqual({ keep: true });
});

it.each([0, 1, 15])('홈 피치 %i개의 목록 구조와 푸터를 유지한다', async (pitchCount) => {
  list = {
    pitches: Array.from({ length: pitchCount }, (_, index) =>
      pitch(`p${index}`, `발표 ${index + 1}`),
    ),
  };
  await render();

  const shell = host.querySelector<HTMLElement>('.pitch-create')!;
  const main = shell.querySelector<HTMLElement>('main')!;
  const section = main.querySelector<HTMLElement>('section[aria-label="발표 목록"]')!;
  const pitchList = section.querySelector<HTMLUListElement>('ul');

  expect(shell.classList.contains('flex-1')).toBe(true);
  expect(main.classList.contains('flex-1')).toBe(true);
  expect(section.querySelectorAll('li[data-pitch-id]')).toHaveLength(pitchCount);
  expect(pitchList === null).toBe(pitchCount === 0);
  expect(pitchList?.className ?? '').not.toContain('overflow-y-auto');
  expect(main.nextElementSibling?.tagName).toBe('FOOTER');
  expect(shell.className).not.toContain('one-screen');
  expect(host.querySelector('footer p[title]')).not.toBeNull();
  expect(host.querySelector('footer p[title]')?.hasAttribute('tabindex')).toBe(false);
});

it('TIP은 8초마다 순환하고 일시정지·재개 버튼으로 타이머를 제어한다', async () => {
  const setIntervalSpy = vi.spyOn(window, 'setInterval');
  const clearIntervalSpy = vi.spyOn(window, 'clearInterval');
  await render();

  const timers = () => setIntervalSpy.mock.calls.filter(([, delay]) => delay === 8000);
  expect(timers()).toHaveLength(1);
  const tip = host.querySelector<HTMLElement>('footer p[title]')!;
  expect(tip.textContent).toBe('연습 전, 슬라이드와 대본을 한 번 확인해 보세요.');

  const next = timers()[0][0];
  expect(typeof next).toBe('function');
  await act(async () => (next as () => void)());
  expect(tip.textContent).toBe('시작 전 세팅에서 대본 표시 방식을 선택할 수 있어요.');

  await click(host.querySelector<HTMLButtonElement>('button[aria-label="팁 자동 전환 일시정지"]')!);
  expect(clearIntervalSpy).toHaveBeenCalled();
  expect(host.querySelector('button[aria-label="팁 자동 전환 재개"]')).not.toBeNull();

  await click(host.querySelector<HTMLButtonElement>('button[aria-label="팁 자동 전환 재개"]')!);
  expect(timers()).toHaveLength(2);
});

it('TIP 문구를 가리키는 동안 자동 전환을 멈췄다가 다시 시작한다', async () => {
  const setIntervalSpy = vi.spyOn(window, 'setInterval');
  const clearIntervalSpy = vi.spyOn(window, 'clearInterval');
  await render();

  const tip = host.querySelector<HTMLElement>('footer p[title]')!;
  await act(async () => {
    tip.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
  });
  expect(clearIntervalSpy).toHaveBeenCalled();
  expect(setIntervalSpy.mock.calls.filter(([, delay]) => delay === 8000)).toHaveLength(1);

  await act(async () => {
    tip.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }));
  });
  expect(setIntervalSpy.mock.calls.filter(([, delay]) => delay === 8000)).toHaveLength(2);
});

it('움직임 감소 설정이면 TIP 자동 순환을 초기 중단하고 수동 재개를 허용한다', async () => {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches: true,
      media: '(prefers-reduced-motion: reduce)',
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
  const setIntervalSpy = vi.spyOn(window, 'setInterval');
  await render();

  expect(host.querySelector('button[aria-label="팁 자동 전환 재개"]')).not.toBeNull();
  expect(setIntervalSpy.mock.calls.filter(([, delay]) => delay === 8000)).toHaveLength(0);

  await click(host.querySelector<HTMLButtonElement>('button[aria-label="팁 자동 전환 재개"]')!);
  expect(setIntervalSpy.mock.calls.filter(([, delay]) => delay === 8000)).toHaveLength(1);
});

it('TIP 목록은 채팅 에이전트 안내를 포함하고 마지막 다음에는 첫 TIP으로 돌아간다', async () => {
  const setIntervalSpy = vi.spyOn(window, 'setInterval');
  await render();
  const tick = setIntervalSpy.mock.calls.find(([, delay]) => delay === 8000)?.[0] as () => void;
  expect(tick).toBeTypeOf('function');
  const tip = host.querySelector<HTMLElement>('footer p[title]')!;

  await act(async () => {
    for (let i = 0; i < 10; i += 1) tick();
  });
  expect(tip.textContent).toBe('왼쪽 아래 채팅 에이전트에게 지난 Take 기록이나 발표 방법을 물어보세요.');

  await act(async () => tick());
  expect(tip.textContent).toBe('연습 전, 슬라이드와 대본을 한 번 확인해 보세요.');
});
