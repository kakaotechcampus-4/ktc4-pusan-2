// @vitest-environment jsdom
import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ScriptMode } from '@/types/api';
import { ScriptModeChoice } from './ScriptModeChoice';

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
function Choice() {
  const [value, setValue] = useState<ScriptMode>('HIGHLIGHT');
  return <ScriptModeChoice value={value} onChange={setValue} />;
}
/** 시작을 누른 뒤 바꾸면 화면과 만들어질 Take 의 대본 표시가 갈라집니다 (#719) */
it('잠그면 눌러도 바뀌지 않는다', async () => {
  const onChange = vi.fn();
  await act(async () =>
    root.render(<ScriptModeChoice value="HIGHLIGHT" onChange={onChange} disabled />),
  );
  const options = [...host.querySelectorAll<HTMLButtonElement>('[role="radio"]')];
  expect(options.every((option) => option.disabled)).toBe(true);
  await act(async () => options[0]!.click());
  expect(onChange).not.toHaveBeenCalled();
});

it('keeps one tab stop and wraps arrow selection with focus following the selected option', async () => {
  await act(async () => root.render(<Choice />));
  const options = [...host.querySelectorAll<HTMLButtonElement>('[role="radio"]')];
  await act(async () => options[0]!.focus());
  for (const [key, index] of [
    ['ArrowUp', 2],
    ['ArrowDown', 0],
    ['ArrowRight', 1],
    ['End', 2],
    ['Home', 0],
    ['ArrowLeft', 2],
  ] as const) {
    await act(async () =>
      document.activeElement!.dispatchEvent(
        new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }),
      ),
    );
    expect(document.activeElement).toBe(options[index]);
    expect(options[index]!.getAttribute('aria-checked')).toBe('true');
    expect(host.querySelectorAll('[role="radio"][tabindex="0"]')).toHaveLength(1);
  }
});
