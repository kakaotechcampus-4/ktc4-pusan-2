// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { StageLiveBadge } from './StageLiveBadge';

it('시작 전에는 숨기고 RUNNING에서만 표시하며 ENDING에서는 다시 숨긴다', () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  const host = document.createElement('div');
  const root = createRoot(host);

  const render = (running: boolean) => act(() => root.render(<StageLiveBadge running={running} />));
  const badge = () => host.querySelector('.stage-mission-label');

  try {
    render(false);
    const element = badge();
    expect(element?.getAttribute('data-live')).toBe('false');
    expect(element?.getAttribute('aria-hidden')).toBe('true');

    render(true);
    expect(badge()).toBe(element);
    expect(element?.getAttribute('data-live')).toBe('true');

    render(false);
    expect(badge()).toBe(element);
    expect(element?.getAttribute('data-live')).toBe('false');
  } finally {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  }
});
