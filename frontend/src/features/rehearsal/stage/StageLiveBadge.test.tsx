// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import type { StagePhase } from './rehearsalStore';
import { StageLiveBadge } from './StageLiveBadge';
import { isRehearsalRunning } from './startGate';

it('실제 리허설 준비·시작·종료 조건과 같은 값으로 LIVE 표시를 전환한다', () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  const host = document.createElement('div');
  const root = createRoot(host);

  const render = (stageReady: boolean, phase: StagePhase, started: boolean) =>
    act(() =>
      root.render(
        <StageLiveBadge running={isRehearsalRunning(stageReady, phase, started)} />,
      ),
    );
  const badge = () => host.querySelector('.stage-mission-label');

  try {
    render(false, 'RUNNING', false);
    const element = badge();
    expect(element?.getAttribute('data-live')).toBe('false');
    expect(element?.getAttribute('aria-hidden')).toBe('true');

    // 자료·세션 준비가 끝나도 마이크/서버의 첫 ready 전에는 켜지지 않습니다.
    render(true, 'RUNNING', false);
    expect(element?.getAttribute('data-live')).toBe('false');
    render(false, 'RUNNING', true);
    expect(element?.getAttribute('data-live')).toBe('false');

    render(true, 'RUNNING', true);
    expect(badge()).toBe(element);
    expect(element?.getAttribute('data-live')).toBe('true');

    render(true, 'ENDING', true);
    expect(element?.getAttribute('data-live')).toBe('false');
    render(true, 'ENDED', true);
    expect(element?.getAttribute('data-live')).toBe('false');
  } finally {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  }
});
