// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { expect, it, vi } from 'vitest';
import { ReportFrame } from './ReportFrame';

it('리포트 틀의 본문이 본문 건너뛰기의 목표가 된다', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  await act(async () =>
    root.render(
      <MemoryRouter>
        <ReportFrame title="발표 리포트" report={undefined}>
          내용
        </ReportFrame>
      </MemoryRouter>,
    ),
  );
  const main = host.querySelector('main')!;
  expect(main.id).toBe('main');
  expect(main.tabIndex).toBe(-1);
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
