/** 뷰어의 주소 · 상태 · 보기 전환과 브라우저 이동 · 포커스가 조작에 따라 함께 바뀌는지 확인한다. */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, useLocation, useNavigate } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DesignRoutes } from './DesignGuide';

vi.mock('./catalog', () => ({
  PRODUCT_ORIGIN: 'http://localhost:3000',
  screens: [
    {
      id: 'pitch-create',
      title: '피치 생성',
      group: '연습',
      route: '/pitch/new',
      status: '제품에 있음',
      summary: '자료를 준비합니다.',
      states: [
        { id: 'ready', label: '기본', description: '준비 상태' },
        { id: 'empty', label: '비어 있음', description: '자료가 없습니다.' },
      ],
      rules: ['자료를 확인합니다.'],
      components: ['Button'],
      tokens: ['ink'],
      sources: ['PitchCreatePage.tsx'],
      productNote: '버전 트리를 줄였습니다.',
      productLinks: [{ label: '새 피치', path: '/pitch/new' }],
      next: [{ label: '시작 전 세팅', screenId: 'prepare' }],
    },
    {
      id: 'prepare',
      title: '시작 전 세팅',
      group: '연습',
      route: '/pitch/:id/device-check',
      status: '제품에 있음',
      summary: '장치를 확인합니다.',
      states: [
        { id: 'ready', label: '기본', description: '시작할 수 있습니다.' },
        { id: 'error', label: '오류', description: '마이크를 확인합니다.' },
        { id: 'empty', label: '비어 있음', description: '장치가 없습니다.' },
      ],
      rules: [],
      components: [],
      tokens: [],
      sources: [],
      next: [],
    },
    {
      id: 'new-idea',
      title: '새 화면 시안',
      group: '시안',
      route: '/new-idea',
      status: '시안',
      summary: '제품에 넣을 새 화면입니다.',
      states: [{ id: 'ready', label: '기본', description: '시안' }],
      rules: [],
      components: [],
      tokens: [],
      sources: [],
      next: [],
    },
  ],
}));

// 미리보기 iframe은 jsdom에서 앱을 불러오지 않으므로, 같은 약속(화면 · 상태 · 이동)을 지키는 대역으로 바꾼다
vi.mock('./PreviewFrame', () => ({
  PreviewFrame: ({
    screenId,
    stateId,
    onNavigate,
  }: {
    screenId: string;
    stateId: string;
    onNavigate: (id: string, stateId?: string) => void;
  }) => (
    <article data-preview={`${screenId}:${stateId}`}>
      <button type="button" onClick={() => onNavigate('prepare')}>
        다른 화면
      </button>
      <button type="button" onClick={() => onNavigate(screenId, 'empty')}>
        같은 화면 상태
      </button>
      <button type="button" onClick={() => onNavigate(screenId, stateId)}>
        같은 상태 그대로
      </button>
    </article>
  ),
}));

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

function LocationProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <div>
      <output data-location>
        {location.pathname}
        {location.search}
      </output>
      <button type="button" onClick={() => navigate(-1)}>
        브라우저 뒤로
      </button>
    </div>
  );
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

async function render(...entries: string[]) {
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
        <LocationProbe />
        <DesignRoutes />
      </MemoryRouter>,
    ),
  );
}

const address = () => host.querySelector('[data-location]')?.textContent ?? '';

async function click(label: string, scope: ParentNode = host) {
  const button = [...scope.querySelectorAll('button')].find((item) => item.textContent === label);
  expect(button, `${label} 버튼`).toBeTruthy();
  await act(async () => button!.click());
}

async function choose(select: HTMLSelectElement, value: string) {
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

/** "화면 상태" 고르기만. 머리의 "미리보기 크기"는 뺀다 */
const stateSelects = () =>
  [...host.querySelectorAll<HTMLSelectElement>('select')].filter((select) =>
    select.closest('label')?.textContent?.startsWith('화면 상태'),
  );

it('저장된 상태를 열고, 다른 화면으로 가면 기록을 쌓아 뒤로 가기로 돌아온다', async () => {
  await render('/?screen=pitch-create&state=empty');
  expect(host.querySelector('[data-preview="pitch-create:empty"]')).toBeTruthy();
  expect(host.textContent).toContain('자료가 없습니다.');
  await click('다른 화면');
  expect(host.querySelector('[data-preview="prepare:ready"]')).toBeTruthy();
  expect(document.activeElement?.textContent).toBe('시작 전 세팅');
  expect(address()).toContain('screen=prepare');
  await click('브라우저 뒤로');
  expect(host.querySelector('[data-preview="pitch-create:empty"]')).toBeTruthy();
  expect(document.activeElement?.textContent).toBe('피치 생성');
});

it('같은 화면 안 상태 전환은 기록을 바꿔 끼우고 포커스를 옮기지 않는다', async () => {
  await render('/?screen=prepare', '/?screen=pitch-create&state=ready');
  const select = stateSelects()[0]!;
  select.focus();
  await choose(select, 'empty');
  expect(host.querySelector('[data-preview="pitch-create:empty"]')).toBeTruthy();
  expect(document.activeElement).toBe(select);
  await click('같은 화면 상태');
  await click('브라우저 뒤로');
  // 상태 전환은 기록을 쌓지 않아 한 번에 앞 화면으로 돌아간다
  expect(host.querySelector('[data-preview="prepare:ready"]')).toBeTruthy();
});

it('같은 주소로의 이동은 건너뛰고, 그 뒤의 뒤로 가기는 평소처럼 포커스를 옮긴다', async () => {
  await render('/?screen=prepare', '/?view=all&screen=pitch-create&state=ready');
  const card = host.querySelector('[data-preview="pitch-create:ready"]')!;
  await click('같은 상태 그대로', card);
  expect(address()).toBe('/?view=all&screen=pitch-create&state=ready');
  await click('브라우저 뒤로');
  expect(host.querySelector('[data-preview="prepare:ready"]')).toBeTruthy();
  expect(document.activeElement?.textContent).toBe('시작 전 세팅');
});

it('검색어를 두 목록이 함께 쓰고 결과가 없으면 알린다', async () => {
  await render('/');
  const inputs = [...host.querySelectorAll<HTMLInputElement>('input[type="search"]')];
  expect(inputs).toHaveLength(2);
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(
      inputs[0],
      '없는 화면',
    );
    inputs[0]!.dispatchEvent(new Event('input', { bubbles: true }));
  });
  expect(inputs[1]!.value).toBe('없는 화면');
  expect(host.querySelectorAll('[role="status"]')).toHaveLength(2);
  expect(host.textContent).toContain('검색한 화면이 없습니다.');
});

it('좁은 화면 목록에서 고르면 목록을 닫고 화면 제목으로 포커스를 옮긴다', async () => {
  await render('/');
  const details = host.querySelector('details')!;
  details.open = true;
  await click('시작 전 세팅', details);
  expect(details.open).toBe(false);
  expect(document.activeElement?.textContent).toBe('시작 전 세팅');
});

it('없는 화면 주소는 다른 미리보기를 그리지 않고 알린다', async () => {
  await render('/?screen=unknown');
  expect(host.textContent).toContain('요청한 화면이 없습니다.');
  expect(host.querySelector('[data-preview]')).toBeNull();
});

it('설명 패널에 디자인 값 · 제품과 다른 점 · 제품에서 열기를 보여 준다', async () => {
  vi.stubGlobal('getComputedStyle', () => ({ getPropertyValue: () => '#111111' }));
  await render('/?screen=pitch-create');
  expect(host.textContent).toContain('--color-ink: #111111');
  expect(host.textContent).toContain('버전 트리를 줄였습니다.');
  const links = [...host.querySelectorAll<HTMLAnchorElement>('a[target="_blank"]')];
  const live = links.find((link) => link.textContent === '새 피치 ↗');
  expect(live?.getAttribute('href')).toBe('http://localhost:3000/pitch/new');
  // 화면만 보기는 같은 앱의 frame 주소를 연다
  const frame = links.find((link) => link.textContent?.startsWith('새 탭에서 화면만 보기'));
  expect(frame?.getAttribute('href')).toMatch(/#\/frame\?screen=pitch-create&state=ready$/);
  expect(host.querySelectorAll('main')).toHaveLength(1);
});

it('옛 시안 주소는 같은 ID의 화면으로, 없는 이름은 기본 화면으로 바꿔 연다', async () => {
  await render('/prepare');
  expect(address()).toBe('/?screen=prepare');
  expect(host.querySelector('[data-preview="prepare:ready"]')).toBeTruthy();
  await act(async () => root.render(null));
  await render('/no-such-draft');
  expect(address()).toBe('/');
  expect(host.querySelector('[data-preview="pitch-create:ready"]')).toBeTruthy();
});

it('시안 화면은 제품에 넣을 경로를 보여 준다', async () => {
  await render('/?screen=new-idea');
  expect(host.textContent).toContain('제품에 넣을 경로/new-idea');
});

it('전체 보기에서 카드 상태를 바꿔도 보기 · 고른 화면 · 목록 표시는 그대로다', async () => {
  await render('/?screen=pitch-create');
  await click('전체 화면');
  expect(host.querySelectorAll('[data-preview]')).toHaveLength(3);
  await choose(stateSelects()[1]!, 'error');
  expect(host.querySelector('[data-preview="prepare:error"]')).toBeTruthy();
  expect(address()).toContain('view=all');
  expect(address()).toContain('screen=pitch-create');
  const current = [...host.querySelectorAll('[aria-current="page"]')].map((el) => el.textContent);
  expect(current).toEqual(['피치 생성', '피치 생성']);
});

it('전체 보기의 미리보기 조작: 같은 화면이면 전체 보기를 지키고, 다른 화면이면 개별 보기로', async () => {
  await render('/?view=all&screen=pitch-create');
  await click('같은 화면 상태', host.querySelector('[data-preview="prepare:ready"]')!);
  expect(host.querySelector('[data-preview="prepare:empty"]')).toBeTruthy();
  expect(address()).toContain('view=all');
  await click('다른 화면', host.querySelector('[data-preview="pitch-create:ready"]')!);
  expect(address()).toContain('view=single');
  expect(address()).toContain('screen=prepare');
});

it('전체 보기에서 다른 카드를 바꿔도 고른 화면의 상태를 지킨다', async () => {
  await render('/?view=all&screen=pitch-create&state=empty');
  await choose(stateSelects()[1]!, 'error');
  expect(host.querySelector('[data-preview="pitch-create:empty"]')).toBeTruthy();
  expect(host.querySelector('[data-preview="prepare:error"]')).toBeTruthy();
  expect(address()).toContain('state=empty');
  expect(address()).toContain('state.prepare=error');
});

it('state가 비어 있으면 state.<화면 ID>에 남은 상태로 연다', async () => {
  await render('/?view=all&screen=pitch-create&state.pitch-create=empty');
  expect(host.querySelector('[data-preview="pitch-create:empty"]')).toBeTruthy();
});

it('목록에서 새로 고른 화면은 두 보기 모두 기본 상태로 연다', async () => {
  await render('/?screen=prepare&state=ready&state.pitch-create=empty');
  await click('피치 생성');
  expect(host.querySelector('[data-preview="pitch-create:ready"]')).toBeTruthy();
  await click('전체 화면');
  expect(host.querySelector('[data-preview="pitch-create:ready"]')).toBeTruthy();
});
