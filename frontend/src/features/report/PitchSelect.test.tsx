// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PitchSelect } from './PitchSelect';

const onChange = vi.fn();
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  onChange.mockClear();
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

async function render(value = 'p1') {
  await act(async () =>
    root.render(
      <PitchSelect
        label="피치"
        value={value}
        leading={{ value: '', label: '전체' }}
        options={[
          { value: 'p1', label: '내 발표' },
          { value: 'p2', label: '다른 발표' },
        ]}
        onChange={onChange}
      />,
    ),
  );
}

function box() {
  return host.querySelector<HTMLElement>('[role="combobox"]')!;
}

function options() {
  return [...host.querySelectorAll<HTMLElement>('[role="option"]')];
}

function activeOption() {
  const id = box().getAttribute('aria-activedescendant');
  return id ? document.getElementById(id)?.textContent : null;
}

async function press(key: string) {
  await act(async () => {
    box().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
  });
}

it('이름은 앞의 글자에서 받고 고른 피치를 보인다', async () => {
  await render();
  expect(document.getElementById(box().getAttribute('aria-labelledby')!)?.textContent).toBe('피치');
  expect(box().textContent).toBe('내 발표');
  expect(box().getAttribute('aria-expanded')).toBe('false');
  expect(host.querySelector('[role="listbox"]')).toBeNull();
});

it('↓ 로 열면 고른 항목을 가리키고, 옮긴 뒤 Enter 로 고르면 닫는다', async () => {
  await render();
  await act(async () => box().focus());
  await press('ArrowDown');
  expect(box().getAttribute('aria-expanded')).toBe('true');
  expect(options().map((option) => option.getAttribute('aria-selected'))).toEqual([
    'false',
    'true',
    'false',
  ]);
  expect(activeOption()).toBe('내 발표');

  await press('ArrowDown');
  await press('ArrowDown');
  expect(activeOption()).toBe('다른 발표');
  await press('Enter');
  expect(onChange).toHaveBeenCalledWith('p2');
  expect(box().getAttribute('aria-expanded')).toBe('false');
  expect(document.activeElement).toBe(box());
});

it('Home · End 로 처음과 끝으로 가고, Esc 는 바꾸지 않고 닫는다', async () => {
  await render();
  await press('End');
  expect(activeOption()).toBe('다른 발표');
  await press('Home');
  expect(activeOption()).toBe('전체');
  await press('Escape');
  expect(box().getAttribute('aria-expanded')).toBe('false');
  expect(onChange).not.toHaveBeenCalled();
});

it('마우스로 항목을 누르면 고르고, 이미 고른 항목은 다시 알리지 않는다', async () => {
  await render();
  await act(async () => box().click());
  await act(async () => options()[0]!.click());
  expect(onChange).toHaveBeenCalledWith('');

  await act(async () => box().click());
  await act(async () => options()[1]!.click());
  expect(onChange).toHaveBeenCalledTimes(1);
  expect(host.querySelector('[role="listbox"]')).toBeNull();
});

it('초점이 나가면 고르지 않고 닫는다', async () => {
  await render();
  await act(async () => box().focus());
  await press(' ');
  expect(box().getAttribute('aria-expanded')).toBe('true');
  await act(async () => box().blur());
  expect(box().getAttribute('aria-expanded')).toBe('false');
  expect(onChange).not.toHaveBeenCalled();
});
