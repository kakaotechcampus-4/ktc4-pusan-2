// @vitest-environment jsdom
import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { CONTENT_BROKEN_TEXT, ContentErrorBoundary } from './ContentErrorBoundary';

afterEach(() => vi.unstubAllGlobals());

/** 누르면 열리는 아이 — 경계가 아이를 새로 만들면 열린 상태를 잃습니다 */
function Toggle({ boom = false }: { boom?: boolean }) {
  const [open, setOpen] = useState(false);
  if (boom) throw new Error('boom');
  return (
    <button type="button" onClick={() => setOpen(true)}>
      {open ? '열림' : '닫힘'}
    </button>
  );
}

function setup() {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  const host = document.createElement('div');
  const root = createRoot(host);
  const render = (resetKey: string, boom = false) =>
    act(() =>
      root.render(
        <ContentErrorBoundary resetKey={resetKey}>
          <Toggle boom={boom} />
        </ContentErrorBoundary>,
      ),
    );
  return { host, root, render };
}

it('결과 판이 바뀌어도 정상이면 아이를 새로 만들지 않아 열어 둔 상태가 남는다 (409 뒤 장별 목록)', () => {
  const { host, root, render } = setup();
  try {
    render('MEASURED:0');
    act(() => host.querySelector('button')!.click());
    expect(host.textContent).toBe('열림');
    render('MEASURED:1');
    expect(host.textContent).toBe('열림');
  } finally {
    act(() => root.unmount());
  }
});

it('던지면 안내로 바꾸고, 같은 결과면 그대로, 결과가 바뀌면 다시 그려 본다', () => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  const { host, root, render } = setup();
  try {
    render('MEASURED:0', true);
    expect(host.textContent).toContain(CONTENT_BROKEN_TEXT);
    render('MEASURED:0', false);
    expect(host.textContent).toContain(CONTENT_BROKEN_TEXT);
    render('MEASURED:1', false);
    expect(host.textContent).toBe('닫힘');
  } finally {
    act(() => root.unmount());
    vi.restoreAllMocks();
  }
});
