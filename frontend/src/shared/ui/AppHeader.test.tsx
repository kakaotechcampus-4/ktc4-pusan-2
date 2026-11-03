// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AppHeader } from './AppHeader';
import { InfoPage } from './InfoPage';

vi.mock('@/shared/api/useLogout', () => ({
  useLogout: () => ({ isPending: false, isError: false }),
}));

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
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

async function show(screen: React.ReactNode) {
  await act(async () =>
    root.render(
      <MemoryRouter>
        <AppHeader />
        {screen}
      </MemoryRouter>,
    ),
  );
}

it('상단바보다 앞에 본문 건너뛰기 링크가 있다', async () => {
  await show(
    <InfoPage title="마이페이지" logo={false}>
      내용
    </InfoPage>,
  );
  const links = [...host.querySelectorAll('a')];
  expect(links[0].textContent).toBe('본문으로 건너뛰기');
  expect(links[0].getAttribute('href')).toBe('#main');
  expect(links[0].compareDocumentPosition(host.querySelector('header')!)).toBe(
    Node.DOCUMENT_POSITION_FOLLOWING,
  );
});

it('링크를 누르면 초점이 본문으로 옮겨 가고 주소는 그대로다', async () => {
  await show(
    <InfoPage title="마이페이지" logo={false}>
      내용
    </InfoPage>,
  );
  const main = host.querySelector('main')!;
  expect(main.id).toBe('main');
  expect(main.tabIndex).toBe(-1);
  main.scrollIntoView = vi.fn();
  const click = new MouseEvent('click', { bubbles: true, cancelable: true });
  await act(async () => {
    host.querySelector('a')!.dispatchEvent(click);
  });
  expect(click.defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(main);
  expect(window.location.hash).toBe('');
});

it('본문이 아직 없으면 기본 이동(앵커)에 맡긴다', async () => {
  await act(async () =>
    root.render(
      <MemoryRouter>
        <AppHeader />
      </MemoryRouter>,
    ),
  );
  const click = new MouseEvent('click', { bubbles: true, cancelable: true });
  await act(async () => {
    host.querySelector('a')!.dispatchEvent(click);
  });
  expect(click.defaultPrevented).toBe(false);
});
