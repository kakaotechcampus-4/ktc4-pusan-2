// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { CheckCard } from './CheckCard';

it('keeps status rows separate from actions and excludes the optional voice task from the required count', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  const start = vi.fn();
  try {
    await act(async () =>
      root.render(
        <CheckCard
          title="장비 준비"
          rows={[
            {
              id: 'camera',
              name: '카메라',
              label: '연결됨',
              done: true,
              selection: (
                <select aria-label="카메라">
                  <option>기본 카메라</option>
                </select>
              ),
            },
            {
              id: 'mic',
              name: '마이크',
              label: '입력 확인',
              done: true,
              selection: (
                <select aria-label="마이크">
                  <option>기본 마이크</option>
                </select>
              ),
              trailing: <span>음량 막대</span>,
            },
            {
              id: 'gaze',
              name: '시선 기준',
              label: '준비 중',
              done: false,
              action: { text: '기준 잡기', onClick: start, disabled: true },
            },
            {
              id: 'voice',
              name: '평소 목소리',
              label: '선택',
              done: false,
              optional: true,
              action: { text: '목소리 재기', onClick: start },
            },
          ]}
        />,
      ),
    );
    expect(host.textContent).toContain('필수 2/3 준비');
    expect(host.querySelector('li')!.tagName).toBe('LI');
    expect(host.querySelector('button select')).toBeNull();
    const rows = host.querySelectorAll('li');
    expect(rows[0]!.children).toHaveLength(3);
    expect(rows[0]!.children[1]!.textContent).toContain('연결됨');
    expect(rows[0]!.lastElementChild!.querySelector('select')).not.toBeNull();
    expect(rows[1]!.children[1]!.textContent).toContain('음량 막대');
    expect(rows[1]!.lastElementChild!.querySelector('select')).not.toBeNull();
    expect(rows[2]!.lastElementChild!.querySelector('button')).not.toBeNull();
    const [gaze, voice] = host.querySelectorAll<HTMLButtonElement>('button');
    await act(async () => gaze!.click());
    expect(start).not.toHaveBeenCalled();
    await act(async () => voice!.click());
    expect(start).toHaveBeenCalledTimes(1);
  } finally {
    await act(async () => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  }
});
