// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PitchSelect, type PitchOption } from './PitchSelect';

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

async function render(
  value = 'p1',
  available: PitchOption[] = [
    { value: 'p1', label: '내 발표' },
    { value: 'p2', label: '다른 발표' },
  ],
) {
  await act(async () =>
    root.render(
      <PitchSelect
        label="피치"
        value={value}
        leading={{ value: '', label: '전체' }}
        options={available}
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

async function press(key: string, init: KeyboardEventInit = {}) {
  await act(async () => {
    box().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...init }));
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
  const onDocumentKeyDown = vi.fn();
  document.addEventListener('keydown', onDocumentKeyDown);
  await press('End');
  expect(activeOption()).toBe('다른 발표');
  await press('Home');
  expect(activeOption()).toBe('전체');
  onDocumentKeyDown.mockClear();
  await press('Escape');
  expect(box().getAttribute('aria-expanded')).toBe('false');
  expect(onChange).not.toHaveBeenCalled();
  expect(onDocumentKeyDown).not.toHaveBeenCalled();
  document.removeEventListener('keydown', onDocumentKeyDown);
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

it('Tab 또는 외부 포인터 입력은 값을 바꾸지 않고 목록을 닫는다', async () => {
  await render();
  await act(async () => box().focus());
  await press(' ');
  expect(box().getAttribute('aria-expanded')).toBe('true');
  await press('Tab');
  expect(box().getAttribute('aria-expanded')).toBe('false');
  expect(onChange).not.toHaveBeenCalled();

  await act(async () => box().click());
  const outside = document.createElement('button');
  document.body.append(outside);
  try {
    await act(async () => {
      outside.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    });
    expect(box().getAttribute('aria-expanded')).toBe('false');
    expect(onChange).not.toHaveBeenCalled();
  } finally {
    outside.remove();
  }
});

it('좁은 화면에서도 목록은 버튼 너비를 넘지 않고 긴 피치 제목은 여러 줄로 보여 준다', async () => {
  const longTitle = '졸업 작품 최종 발표와 중간 점검을 함께 준비하는 아주 긴 피치 제목';
  await render('p1', [
    { value: 'p1', label: longTitle },
    { value: 'p2', label: longTitle + ' 두 번째' },
  ]);
  await act(async () => box().click());

  const container = box().parentElement!;
  const list = host.querySelector<HTMLElement>('[role="listbox"]')!;
  expect(container.classList.contains('max-w-full')).toBe(true);
  expect(container.classList.contains('min-w-0')).toBe(true);
  expect(container.classList.contains('flex-1')).toBe(true);
  expect(container.classList.contains('sm:w-xs')).toBe(true);
  expect(container.classList.contains('sm:flex-none')).toBe(true);
  expect(list.classList.contains('inset-x-0')).toBe(true);
  expect(list.classList.contains('w-max')).toBe(false);
  expect(options()[1]!.textContent).toBe(longTitle);
  expect(options()[1]!.querySelector('span')?.classList.contains('truncate')).toBe(false);
  expect(options()[1]!.querySelector('span')?.classList.contains('break-words')).toBe(true);
});

it('목록 여백과 옵션의 mousedown은 초점 이동을 막고 터치 pointerdown은 유지한다', async () => {
  await render();
  await act(async () => box().focus());
  await act(async () => box().click());
  const list = host.querySelector<HTMLElement>('[role="listbox"]')!;
  const scrollbarDown = new MouseEvent('mousedown', {
    bubbles: true,
    cancelable: true,
    button: 0,
  });
  await act(async () => list.dispatchEvent(scrollbarDown));
  expect(scrollbarDown.defaultPrevented).toBe(true);

  const secondaryDown = new MouseEvent('mousedown', {
    bubbles: true,
    cancelable: true,
    button: 2,
  });
  await act(async () => list.dispatchEvent(secondaryDown));
  expect(secondaryDown.defaultPrevented).toBe(false);

  const touchStart = new Event('pointerdown', { bubbles: true, cancelable: true });
  await act(async () => options()[2]!.dispatchEvent(touchStart));
  expect(touchStart.defaultPrevented).toBe(false);
  await act(async () => document.dispatchEvent(new Event('pointerup', { bubbles: true })));
  expect(document.activeElement).toBe(box());
  expect(box().getAttribute('aria-expanded')).toBe('true');

  const optionDown = new MouseEvent('mousedown', {
    bubbles: true,
    cancelable: true,
    button: 0,
  });
  await act(async () => options()[2]!.dispatchEvent(optionDown));
  expect(optionDown.defaultPrevented).toBe(true);
  await act(async () => options()[2]!.click());
  expect(onChange).toHaveBeenCalledWith('p2');
  expect(box().getAttribute('aria-expanded')).toBe('false');
});

it('앱 코드가 외부 요소로 초점을 옮기면 열린 목록을 닫는다', async () => {
  await render();
  const main = document.createElement('main');
  main.tabIndex = -1;
  document.body.append(main);
  try {
    await act(async () => box().focus());
    await act(async () => box().click());
    await act(async () => main.focus());
    expect(document.activeElement).toBe(main);
    expect(box().getAttribute('aria-expanded')).toBe('false');
    expect(host.querySelector('[role="listbox"]')).toBeNull();
  } finally {
    main.remove();
  }
});

it('목록 안 포인터 조작 중 잃은 초점을 포인터 종료 후 복구한다', async () => {
  await render();
  const main = document.createElement('main');
  main.tabIndex = -1;
  document.body.append(main);
  try {
    await act(async () => box().focus());
    await act(async () => box().click());
    const list = host.querySelector<HTMLElement>('[role="listbox"]')!;
    await act(async () => {
      list.dispatchEvent(new Event('pointerdown', { bubbles: true }));
      main.focus();
    });
    expect(box().getAttribute('aria-expanded')).toBe('true');
    await act(async () => document.dispatchEvent(new Event('pointerup', { bubbles: true })));
    expect(document.activeElement).toBe(box());
    await press('Escape');
    expect(box().getAttribute('aria-expanded')).toBe('false');
  } finally {
    main.remove();
  }
});

it('열린 상태에서 창 크기가 바뀌면 목록을 닫는다', async () => {
  await render();
  await act(async () => box().click());
  await act(async () => window.dispatchEvent(new Event('resize')));
  expect(box().getAttribute('aria-expanded')).toBe('false');
  expect(host.querySelector('[role="listbox"]')).toBeNull();
});

it('창이 비활성화되면 목록을 닫는다', async () => {
  await render();
  await act(async () => box().click());
  await act(async () => window.dispatchEvent(new Event('blur')));
  expect(box().getAttribute('aria-expanded')).toBe('false');
});

it('열린 목록에서 Alt+위쪽 화살표는 현재 항목을 선택하고 닫는다', async () => {
  await render();
  await press('End');
  expect(activeOption()).toBe('다른 발표');
  await press('ArrowUp', { altKey: true });
  expect(onChange).toHaveBeenCalledWith('p2');
  expect(box().getAttribute('aria-expanded')).toBe('false');
});

it('위쪽에 목록 전체가 들어갈 때만 위로 펼친다', async () => {
  await render();
  vi.stubGlobal('innerHeight', 320);
  const trigger = box();
  const originalRect = trigger.getBoundingClientRect;
  let top = 260;
  let bottom = 300;
  trigger.getBoundingClientRect = () => ({ top, bottom }) as DOMRect;
  const originalHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
    configurable: true,
    get() {
      return this.getAttribute('role') === 'listbox' ? 200 : 0;
    },
  });
  try {
    await act(async () => trigger.click());
    const list = host.querySelector<HTMLElement>('[role="listbox"]')!;
    expect(list.classList.contains('bottom-full')).toBe(true);
    expect(list.classList.contains('top-full')).toBe(false);

    // 위쪽이 아래보다 넓더라도 목록 높이에 못 미치면 아래로 열어 첫 항목을 보존합니다.
    await act(async () => trigger.click());
    top = 150;
    bottom = 190;
    await act(async () => trigger.click());
    const reopenedList = host.querySelector<HTMLElement>('[role="listbox"]')!;
    expect(reopenedList.classList.contains('top-full')).toBe(true);
    expect(reopenedList.classList.contains('bottom-full')).toBe(false);
  } finally {
    trigger.getBoundingClientRect = originalRect;
    if (originalHeight) {
      Object.defineProperty(HTMLElement.prototype, 'offsetHeight', originalHeight);
    } else {
      delete (HTMLElement.prototype as { offsetHeight?: number }).offsetHeight;
    }
  }
});
