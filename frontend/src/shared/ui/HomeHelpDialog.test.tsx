// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { HomeHelpDialog } from './HomeHelpDialog';

const showModalDescriptor = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'showModal');
const closeDescriptor = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'close');

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let onDismiss = vi.fn<() => void>();

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.open = true;
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.open = false;
      this.dispatchEvent(new Event('close'));
    },
  });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  onDismiss = vi.fn<() => void>();
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  if (showModalDescriptor) {
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', showModalDescriptor);
  } else {
    Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal');
  }
  if (closeDescriptor) {
    Object.defineProperty(HTMLDialogElement.prototype, 'close', closeDescriptor);
  } else {
    Reflect.deleteProperty(HTMLDialogElement.prototype, 'close');
  }
  vi.unstubAllGlobals();
});

async function renderHelp() {
  await act(async () => {
    root.render(<HomeHelpDialog onDismiss={onDismiss} />);
  });
}

it('기존 사용방법 3단계를 유지하고 두 번째 단계에 두 화면을 안내한다', async () => {
  await renderHelp();

  const steps = [...host.querySelectorAll('dialog > div > ol > li')];
  expect(steps).toHaveLength(3);
  expect(steps.map((step) => step.querySelector('h3')?.textContent)).toEqual([
    '1단계 자료 준비',
    '2단계 장치 점검과 연습',
    '3단계 연습 기록 확인',
  ]);
  expect(steps[1].querySelectorAll('figure')).toHaveLength(2);
  expect(steps[0].querySelectorAll('figure')).toHaveLength(0);
  expect(steps[2].querySelectorAll('figure')).toHaveLength(0);
});

it('개인정보 없는 SVG 목업에 대체 텍스트와 키보드로 여는 확대 링크가 있다', async () => {
  await renderHelp();

  const images = [...host.querySelectorAll('figure img')];
  expect(images.map((image) => image.getAttribute('src'))).toEqual([
    '/help/device-check.svg',
    '/help/rehearsal-stage.svg',
  ]);
  for (const image of images) {
    expect(image.getAttribute('alt')?.length).toBeGreaterThan(40);
    const link = image.closest('figure')?.querySelector('a');
    expect(link?.getAttribute('href')).toBe(image.getAttribute('src'));
    expect(link?.getAttribute('target')).toBe('_blank');
    expect(link?.getAttribute('rel')).toBe('noopener noreferrer');
  }
  expect(host.textContent).toContain('개인정보가 없는 설명용 목업');
});

it('리허설의 일곱 기능과 키보드 조작을 이미지와 별도 텍스트로 제공한다', async () => {
  await renderHelp();

  const numbered = host.querySelectorAll('ol[aria-label="발표 연습 화면의 번호별 사용방법"] > li');
  expect(numbered).toHaveLength(7);
  for (const label of ['슬라이드', '카메라', '마이크', '피코 코치', '대본', '이동 방법', '발표 끝내기']) {
    expect([...numbered].some((row) => row.textContent?.includes(label))).toBe(true);
  }
  expect(host.textContent).toContain('Space 다음 장');
  expect(host.textContent).toContain('↑·↓');
});

it('닫기 버튼이 모달을 닫고 언마운트 시 본문 스크롤을 복원한다', async () => {
  const previousOverflow = document.body.style.overflow;
  await renderHelp();

  const dialog = host.querySelector('dialog')!;
  expect(dialog.open).toBe(true);
  expect(dialog.getAttribute('aria-labelledby')).toBeTruthy();
  expect(document.body.style.overflow).toBe('hidden');

  await act(async () => {
    host.querySelector('dialog header button')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  expect(dialog.open).toBe(false);
  expect(onDismiss).toHaveBeenCalledOnce();

  await act(async () => root.unmount());
  expect(document.body.style.overflow).toBe(previousOverflow);
});
