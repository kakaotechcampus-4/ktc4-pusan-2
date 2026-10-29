/** 미리보기 iframe이 화면 크기대로 그려지고, iframe 안 이동을 뷰어에 넘기는지(그리고 반대로 화면만 보기에서 스스로 이동하는지) 확인한다. */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DesignRoutes } from './DesignGuide';
import { FRAME_MESSAGE } from './frame';
import { PreviewFrame } from './PreviewFrame';

let host: HTMLDivElement;
let root: Root;

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
  vi.restoreAllMocks();
});

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-location>
      {location.pathname}
      {location.search}
    </output>
  );
}

async function renderFrame(
  onNavigate = vi.fn(),
  size: { width: number; height: number } | null = null,
) {
  await act(async () =>
    root.render(
      <PreviewFrame
        screenId="prepare-mission"
        stateId="mic-error"
        onNavigate={onNavigate}
        size={size}
        fitHeight={384}
        title="시작 전 세팅 미리보기 화면"
      />,
    ),
  );
  return host.querySelector('iframe')!;
}

it('iframe은 같은 앱의 화면만 보기 주소를 열고, 그 iframe에서 온 이동만 뷰어에 넘긴다', async () => {
  const onNavigate = vi.fn();
  const iframe = await renderFrame(onNavigate);
  expect(iframe.getAttribute('src')).toMatch(/#\/frame\?screen=prepare-mission&state=mic-error$/);
  const navigate = { kind: FRAME_MESSAGE, type: 'navigate', screenId: 'stage', stateId: 'exam' };
  const send = (source: Window | null, origin = window.location.origin) =>
    act(async () => {
      window.dispatchEvent(new MessageEvent('message', { data: navigate, source, origin }));
    });
  await send(window);
  await send(iframe.contentWindow, 'http://example.com');
  expect(onNavigate).not.toHaveBeenCalled();
  await send(iframe.contentWindow);
  expect(onNavigate).toHaveBeenCalledWith('stage', 'exam');
});

it('칸보다 넓은 화면 크기는 그 크기로 그린 뒤 칸 폭에 맞게 줄인다', async () => {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(640);
  const iframe = await renderFrame(vi.fn(), { width: 1280, height: 800 });
  expect(iframe.style.width).toBe('1280px');
  expect(iframe.style.height).toBe('800px');
  expect(iframe.style.transform).toBe('scale(0.5)');
  expect(iframe.parentElement!.style.height).toBe('400px');
});

async function renderRoute(path: string) {
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={[path]}>
        <LocationProbe />
        <DesignRoutes />
      </MemoryRouter>,
    ),
  );
}

async function clickButton(label: string) {
  const button = [...host.querySelectorAll('button')].find((item) => item.textContent === label);
  expect(button, `${label} 버튼`).toBeTruthy();
  await act(async () => button!.click());
}

it('iframe 안의 화면만 보기는 화면 안 이동을 뷰어에 메시지로 넘긴다', async () => {
  const postMessage = vi.fn();
  vi.stubGlobal('parent', { postMessage });
  await renderRoute('/frame?screen=prepare-mission&state=default');
  expect(host.querySelector('[data-location]')?.textContent).toContain('/frame');
  await clickButton('Take 3 시작하기');
  expect(postMessage).toHaveBeenCalledWith(
    { kind: FRAME_MESSAGE, type: 'navigate', screenId: 'stage', stateId: 'default' },
    window.location.origin,
  );
});

it('새 탭에서 연 화면만 보기는 그 주소에서 바로 이동한다', async () => {
  await renderRoute('/frame?screen=prepare-mission&state=default');
  await clickButton('Take 3 시작하기');
  expect(host.querySelector('[data-location]')?.textContent).toBe(
    '/frame?screen=stage&state=default',
  );
  expect(host.textContent).toContain('중간 종료');
});

it('칸보다 좁은 화면은 줄이지 않고 가운데에 둔다', async () => {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(900);
  const iframe = await renderFrame(vi.fn(), { width: 390, height: 844 });
  expect(iframe.style.transform).toBe('');
  expect(iframe.parentElement!.className).toContain('mx-auto');
  expect(iframe.parentElement!.style.width).toBe('390px');
});
