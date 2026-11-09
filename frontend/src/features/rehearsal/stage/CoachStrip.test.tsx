// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { CoachStrip } from './CoachStrip';

it('실제 메시지만 알림으로 읽고 실전 모드에서는 메시지와 상태를 숨긴다', () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  const host = document.createElement('div');
  const root = createRoot(host);
  try {
    act(() => root.render(<CoachStrip message={null} />));
    const live = host.querySelector('[role="status"]');
    expect(live?.textContent).toBe('');
    expect(host.textContent).toContain('코치 안내가 여기에 표시돼요');
    act(() => root.render(<CoachStrip message="조금만 천천히 말해 볼까요?" />));
    expect(host.querySelector('[role="status"]')).toBe(live);
    expect(live?.textContent).toBe('조금만 천천히 말해 볼까요?');
    expect(host.querySelector('.coach-idle')).toBeNull();
    act(() =>
      root.render(
        <CoachStrip message="이 문구는 실전에서 보이면 안 돼요" exam>
          <span>상태</span>
        </CoachStrip>,
      ),
    );
    expect(live?.textContent).toBe('');
    expect(host.textContent).toContain('코치 OFF');
    expect(host.textContent).not.toContain('상태');
  } finally {
    act(() => root.unmount());
    vi.unstubAllGlobals();
  }
});
