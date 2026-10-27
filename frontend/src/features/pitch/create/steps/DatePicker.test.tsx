// @vitest-environment jsdom
import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DatePicker } from './DatePicker';

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
function Calendar() {
  const [value, setValue] = useState('2028-02-28');
  return <DatePicker value={value} onChange={setValue} />;
}
const day = (iso: string) => host.querySelector<HTMLButtonElement>(`[data-date="${iso}"]`)!;
async function key(value: string) {
  await act(async () =>
    document.activeElement!.dispatchEvent(
      new KeyboardEvent('keydown', { key: value, bubbles: true, cancelable: true }),
    ),
  );
}
it('moves across leap day and month boundaries without changing the selected date', async () => {
  await act(async () => root.render(<Calendar />));
  await act(async () => day('2028-02-28').focus());
  await key('ArrowRight');
  expect(document.activeElement).toBe(day('2028-02-29'));
  await key('ArrowRight');
  expect(document.activeElement).toBe(day('2028-03-01'));
  expect(host.textContent).toContain('2028년 3월');
  expect(day('2028-02-28').parentElement?.getAttribute('aria-selected')).toBe('true');
  await act(async () => day('2028-03-01').click());
  expect(day('2028-03-01').parentElement?.getAttribute('aria-selected')).toBe('true');
  expect(host.querySelectorAll('[data-date][tabindex="0"]')).toHaveLength(1);
});
it('supports week movement and Home/End with valid grid rows', async () => {
  await act(async () => root.render(<Calendar />));
  await act(async () => day('2028-02-28').focus());
  await key('ArrowDown');
  expect(document.activeElement).toBe(day('2028-03-06'));
  await key('ArrowUp');
  expect(document.activeElement).toBe(day('2028-02-28'));
  await key('Home');
  expect(document.activeElement).toBe(day('2028-02-27'));
  await key('End');
  expect(document.activeElement).toBe(day('2028-03-04'));
  expect(host.querySelectorAll('thead tr th')).toHaveLength(7);
  expect([...host.querySelectorAll('tbody tr')].every((row) => row.children.length === 7)).toBe(
    true,
  );
});
