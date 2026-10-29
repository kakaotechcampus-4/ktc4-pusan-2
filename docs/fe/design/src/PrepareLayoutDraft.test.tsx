import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { entries } from './catalog';
import { ScreenPreview } from './ScreenPreview';
import { prepareDraftFixture } from './screens/prepareDraftFixtures';

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const navigate = vi.fn();
const media = vi.fn();
const fetch = vi.fn();
const originalMedia = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices');
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('fetch', fetch);
  fetch.mockReset();
  media.mockReset();
  navigate.mockReset();
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: media },
  });
  vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(600);
  vi.spyOn(Element.prototype, 'scrollWidth', 'get').mockImplementation(function (this: Element) {
    return (this.textContent?.length ?? 0) > 100 ? 1200 : 200;
  });
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(18);
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  expect(fetch).not.toHaveBeenCalled();
  expect(media).not.toHaveBeenCalled();
  host.remove();
  if (originalMedia) Object.defineProperty(navigator, 'mediaDevices', originalMedia);
  else Reflect.deleteProperty(navigator, 'mediaDevices');
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function render(stateId = 'default') {
  await act(async () =>
    root.render(
      <MemoryRouter>
        <ScreenPreview
          key={stateId}
          screenId="prepare-layout-review"
          stateId={stateId}
          onNavigate={navigate}
        />
      </MemoryRouter>,
    ),
  );
}
function button(label: string) {
  return [...host.querySelectorAll<HTMLButtonElement>('button')].find(
    (node) => node.textContent?.trim() === label,
  )!;
}
async function click(label: string) {
  await act(async () => button(label).click());
}

it('is explicitly a PM draft and keeps the product sample available separately', () => {
  expect(entries.find((entry) => entry.id === 'prepare-layout-review')?.status).toBe('시안');
  expect(entries.find((entry) => entry.id === 'prepare-layout-review')?.summary).toContain(
    'PM 승인 전',
  );
  expect(entries.find((entry) => entry.id === 'prepare-mission')?.status).toBe('제품에 있음');
});

it('uses independent device examples and allows mode selection before devices are ready', async () => {
  await render();
  expect(host.textContent).toContain('필수 0/3 준비');
  expect(button('Take 3 시작하기').disabled).toBe(true);
  expect(host.querySelectorAll('select')).toHaveLength(2);
  expect(host.querySelectorAll('button select')).toHaveLength(0);
  const modes = host.querySelectorAll<HTMLButtonElement>('[role="radio"]');
  expect([...modes].every((mode) => !mode.disabled)).toBe(true);
  await act(async () => modes[1]!.click());
  await click('카메라·마이크 켜기');
  expect(host.textContent).toContain('필수 2/3 준비');
  await click('시선 기준 잡기');
  expect(host.textContent).toContain('필수 3/3 완료');
  await click('Take 3 시작하기');
  expect(navigate).toHaveBeenCalledWith('stage', 'keyword');
});

it('keeps optional voice reading and all mission access inside the draft', async () => {
  await render('ready');
  expect(button('Take 3 시작하기').disabled).toBe(false);
  await click('목소리 재기');
  expect(host.querySelector('.prepare-draft-mission-list')).toBeNull();
  expect(button('전체 미션 보기')).toBeDefined();
  await click('다 읽었어요');
  expect(host.textContent).toContain('실제 소리를 측정하지 않았어요');
  expect(button('Take 3 시작하기').disabled).toBe(false);
});

it('shows only actual mission rows including the last page', async () => {
  for (const [stateId, count] of [
    ['one', 1],
    ['two', 2],
    ['ready', 3],
    ['four', 4],
    ['many', 20],
  ] as const) {
    await render(stateId);
    expect(host.querySelectorAll('.prepare-draft-mission-list li')).toHaveLength(
      Math.min(count, 3),
    );
    for (let page = 1; page < Math.ceil(count / 3); page++) await click('다음');
    expect(host.querySelectorAll('.prepare-draft-mission-list li')).toHaveLength(
      ((count - 1) % 3) + 1,
    );
  }
  await render('empty');
  expect(host.querySelectorAll('.prepare-draft-mission-list li')).toHaveLength(0);
  expect(host.textContent).toContain('리뷰에서 정한 미션이 없어요');
});

it('opens the full 500-character description only for clipped text and restores focus', async () => {
  await render('ready');
  expect(button('더 보기')).toBeUndefined();
  await render('long');
  const trigger = host.querySelector<HTMLButtonElement>('[aria-label="미션 1 전체 보기"]')!;
  await act(async () => {
    trigger.focus();
    trigger.click();
  });
  expect(host.querySelector<HTMLDialogElement>('dialog')?.open).toBe(true);
  expect(host.querySelector('.prepare-draft-mission-description')?.textContent).toHaveLength(500);
  await click('닫기');
  expect(document.activeElement).toBe(trigger);
  expect(prepareDraftFixture('long').info.missions?.items).toHaveLength(20);
});

it('shows start locks and exam navigation without starting a product take', async () => {
  await render('starting');
  expect(
    [...host.querySelectorAll<HTMLButtonElement>('[role="radio"]')].every((mode) => mode.disabled),
  ).toBe(true);
  expect(
    [...host.querySelectorAll<HTMLSelectElement>('select')].every((select) => select.disabled),
  ).toBe(true);
  expect(button('Take 3 시작하기').disabled).toBe(true);
  await render('exam');
  await click('Take 3 실전 모드로 시작');
  expect(navigate).toHaveBeenCalledWith('stage', 'exam');
});
