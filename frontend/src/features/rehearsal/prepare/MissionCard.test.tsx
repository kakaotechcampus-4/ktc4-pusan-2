// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MissionCard } from './MissionCard';
import type { PrepareInfo } from './prepareInfo';

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let availableWidth = 600;
let resize: (() => void)[] = [];
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  availableWidth = 600;
  resize = [];
  vi.spyOn(Element.prototype, 'clientWidth', 'get').mockImplementation(() => availableWidth);
  vi.spyOn(Element.prototype, 'scrollWidth', 'get').mockImplementation(function (this: Element) {
    return (this.textContent?.length ?? 0) > 100 ? 1200 : 300;
  });
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(18);
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(measure: () => void) {
        resize.push(measure);
      }
      observe() {}
      disconnect() {}
    },
  );
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
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function info(count: number): PrepareInfo {
  return {
    title: '발표',
    timeLimitSec: 300,
    presentation: null,
    script: null,
    newerScript: null,
    nextTakeNumber: 5,
    defaultScriptMode: 'HIGHLIGHT',
    missions: {
      sourceTakeNumber: 2,
      items: Array.from({ length: count }, (_, i) => ({
        id: `m${i}`,
        description: `미션 ${i + 1}: ${'청중을 바라보며 말해요. '.repeat(40)}`.slice(0, 500),
      })),
    },
  };
}
const button = (label: string) =>
  host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;

it('pages all twenty missions without treating the visible three as the whole Take', async () => {
  await act(async () => root.render(<MissionCard info={info(20)} exam={false} />));
  expect(host.querySelectorAll('ol li')).toHaveLength(3);
  expect(host.textContent).toContain('전체 20개가 이번 Take에 적용돼요.');
  expect(button('이전 미션 페이지').disabled).toBe(true);
  const observed: number[] = [];
  for (let page = 0; page < 7; page++) {
    observed.push(
      ...[...host.querySelectorAll('.prepare-mission-number')].map((node) =>
        Number(node.textContent),
      ),
    );
    if (page < 6) await act(async () => button('다음 미션 페이지').click());
  }
  expect(observed).toEqual(Array.from({ length: 20 }, (_, i) => i + 1));
  expect(button('다음 미션 페이지').disabled).toBe(true);
  expect(host.textContent).toContain('19–20 / 20');
  expect(host.textContent).toContain('Take 2 리뷰에서 정한 미션이에요');
  expect(host.textContent).not.toContain('분석 중');
});

it('reads the full 500-character description, moves between missions, and restores focus on Escape', async () => {
  const source = info(20);
  await act(async () => root.render(<MissionCard info={source} exam />));
  const trigger = button('미션 1 전체 보기');
  trigger.focus();
  await act(async () => trigger.click());
  const dialog = host.querySelector('dialog')!;
  expect(dialog.open).toBe(true);
  expect(dialog.querySelector('.prepare-mission-description')!.textContent).toBe(
    source.missions!.items[0]!.description,
  );
  expect(dialog.textContent).toContain('끝난 뒤 리포트에서 결과를 확인해요');
  const next = [...dialog.querySelectorAll('button')].find(
    (node) => node.textContent === '다음 미션',
  )!;
  await act(async () => next.click());
  expect(dialog.querySelector('.prepare-mission-description')!.textContent).toBe(
    source.missions!.items[1]!.description,
  );
  await act(async () => dialog.dispatchEvent(new Event('cancel', { cancelable: true })));
  expect(host.querySelector('dialog')).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

it('shows the complete short mission without a button and adds reading access only when it no longer fits', async () => {
  const short = info(1);
  short.missions!.items[0]!.description = '문장 끝에서 청중을 바라보기';
  await act(async () => root.render(<MissionCard info={short} exam={false} />));
  expect(host.querySelectorAll('ol li')).toHaveLength(1);
  expect(host.querySelector('ol p')!.textContent).toBe('문장 끝에서 청중을 바라보기');
  expect(host.querySelector('ol button')).toBeNull();
  await act(async () => {
    availableWidth = 240;
    resize.forEach((measure) => measure());
  });
  expect(button('미션 1 전체 보기')).not.toBeNull();
  await act(async () => {
    availableWidth = 600;
    resize.forEach((measure) => measure());
  });
  expect(host.querySelector('ol button')).toBeNull();
});

it('keeps first Take and reviewed-but-empty missions distinct and clamps a shrunken list', async () => {
  await act(async () =>
    root.render(<MissionCard info={{ ...info(0), missions: null }} exam={false} />),
  );
  expect(host.textContent).toContain('첫 Take라 아직 미션이 없어요');
  await act(async () => root.render(<MissionCard info={info(0)} exam={false} />));
  expect(host.textContent).toContain('Take 2 리뷰에서 정한 미션이 없어요');
  await act(async () => root.render(<MissionCard info={info(20)} exam={false} />));
  await act(async () => button('다음 미션 페이지').click());
  await act(async () => root.render(<MissionCard info={info(1)} exam={false} />));
  expect(host.querySelectorAll('ol li')).toHaveLength(1);
  expect(host.querySelector('.prepare-mission-number')!.textContent).toBe('1');
});

it('keeps all missions readable while the optional voice task uses the compact layout', async () => {
  await act(async () => root.render(<MissionCard info={info(20)} exam={false} compact />));
  expect(host.querySelector('ol')).toBeNull();
  const open = [...host.querySelectorAll('button')].find(
    (node) => node.textContent === '전체 미션 보기',
  )!;
  await act(async () => open.click());
  expect(host.querySelector('dialog')!.textContent).toContain('1 / 20');
});
