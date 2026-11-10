// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { HomeHelpDialog } from './HomeHelpDialog';

const showModalDescriptor = Object.getOwnPropertyDescriptor(
  HTMLDialogElement.prototype,
  'showModal',
);
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
    root.render(
      <MemoryRouter>
        <HomeHelpDialog onDismiss={onDismiss} />
      </MemoryRouter>,
    );
  });
}

it('기존 사용방법 3단계를 유지하고 장치·연습·채팅 화면을 안내한다', async () => {
  await renderHelp();

  // 단계 밖의 튜토리얼 안내 제목(h3)은 단계 수에 포함하지 않습니다 (#1105).
  const steps = [...host.querySelectorAll('dialog > div > ol > li > div > h3')];
  expect(steps).toHaveLength(3);
  expect(steps.map((step) => step.textContent)).toEqual([
    '1단계 자료 준비',
    '2단계 장치 점검과 연습',
    '3단계 연습 기록 확인',
  ]);
  expect(steps[1].closest('li')?.querySelectorAll('figure')).toHaveLength(2);
  expect(steps[0].closest('li')?.querySelectorAll('figure')).toHaveLength(0);
  expect(steps[2].closest('li')?.querySelectorAll('figure')).toHaveLength(1);
});

it('개인정보 없는 SVG 목업에 대체 텍스트와 키보드로 여는 확대 링크가 있다', async () => {
  await renderHelp();

  const images = [...host.querySelectorAll('figure img')];
  expect(images.map((image) => image.getAttribute('src'))).toEqual([
    '/help/device-check.svg',
    '/help/rehearsal-stage.svg',
    '/help/chat-agent.svg',
  ]);
  expect(images[0].getAttribute('alt')).toContain('이번 Take 미션');
  expect(images[0].getAttribute('alt')).toContain('기준 목소리');
  expect(host.textContent).toContain('장치 점검 중에는 새미나이 채팅 버튼이 보이지 않아요.');
  expect(images[1].getAttribute('alt')).toContain('8 시선 테두리');
  expect(images[1].getAttribute('alt')).toContain('6 코치 아래 마우스·키보드 이동 안내');
  expect(images[2].getAttribute('alt')).toContain('대화 범위');
  expect(images[2].getAttribute('alt')).toContain('기억 설정');
  for (const [index, image] of images.entries()) {
    const link = image.closest('figure')?.querySelector('a');
    expect(link?.getAttribute('aria-label')).toBe(
      `${['장치 점검 화면', '실시간 코칭 모드 예시', '새미나이 채팅 화면'][index]} 이미지 크게 보기 (새 탭)`,
    );
    expect(link?.getAttribute('href')).toBe(image.getAttribute('src'));
    expect(link?.getAttribute('target')).toBe('_blank');
    expect(link?.getAttribute('rel')).toBe('noopener noreferrer');
  }
  expect(host.textContent).toContain('설명용 목업 (작은 화면에서는 가로 스크롤 가능)');
  expect(host.textContent).toContain('Google Chrome');
  expect(host.textContent).toContain('Edge에서는 시선 추적');
  expect(
    host.querySelectorAll('figure [tabindex="0"][aria-label$="가로 스크롤 영역"]'),
  ).toHaveLength(3);
});

it('리허설의 여덟 기능과 키보드 조작을 이미지와 별도 텍스트로 제공한다', async () => {
  await renderHelp();

  const numbered = host.querySelectorAll('ol[aria-label="발표 연습 화면의 번호별 사용방법"] > li');
  expect(numbered).toHaveLength(8);
  for (const label of [
    '슬라이드',
    '카메라',
    '마이크',
    '피코 코치',
    '대본',
    '이동 방법',
    '발표 끝내기',
    '시선 테두리',
  ]) {
    expect([...numbered].some((row) => row.textContent?.includes(label))).toBe(true);
  }
  expect(host.textContent).toContain('왼쪽 클릭하면 다음 장');
  expect(host.textContent).toContain('오른쪽 클릭하면 이전 장');
  expect(host.textContent).toContain('Space로도 넘기고');
  expect(host.textContent).toContain('↑·↓');
  expect(host.textContent).toContain('자동으로 끝날 수도 있어요');
});

it('세 설명용 SVG가 올바른 문서이며 현재 화면의 필수 문구를 포함한다', () => {
  const device = readFileSync(resolve('public/help/device-check.svg'), 'utf8');
  const stage = readFileSync(resolve('public/help/rehearsal-stage.svg'), 'utf8');
  const chat = readFileSync(resolve('public/help/chat-agent.svg'), 'utf8');

  for (const content of [device, stage, chat]) {
    const parsed = new DOMParser().parseFromString(content, 'image/svg+xml');
    expect(parsed.querySelector('parsererror')).toBeNull();
    expect(parsed.documentElement.tagName).toBe('svg');
  }

  const deviceSvg = new DOMParser().parseFromString(device, 'image/svg+xml');
  expect(deviceSvg.querySelector('[aria-label*="채팅 에이전트 런처"]')).toBeNull();
  expect(deviceSvg.querySelector('desc')?.textContent).not.toContain('채팅 런처');

  const chatSvg = new DOMParser().parseFromString(chat, 'image/svg+xml');
  expect(chatSvg.querySelector('[data-help-chat-panel]')?.getAttribute('transform')).toBe(
    'translate(523 0)',
  );

  for (const phrase of [
    'TAKE 3',
    '이번 Take 미션',
    '발표 중 대본 표시',
    '시작 후 변경 불가',
    '전체 대본 + 하이라이트',
    '핵심 키워드만',
    '실전 모드 · 대본 없이',
    'Take 3 시작하기',
    '2분 넘게',
    '기준 목소리',
  ]) {
    expect(device).toContain(phrase);
  }
  for (const phrase of [
    '마지막 장에서 끝낼 수 있어요',
    '테두리 =',
    '청중',
    '화면',
    '대본',
    '판정 불가',
    '클릭 다음',
    '우클릭 이전',
  ]) {
    expect(stage).toContain(phrase);
  }

  for (const phrase of [
    '새미나이',
    '새 대화',
    '대화 목록',
    '기억 설정',
    '기억 없이 대화하기',
    '범위: 전체',
    '이런 걸 도와드릴 수 있어요',
    '다음 연습 목표 잡아 줘',
    '메시지 입력',
  ]) {
    expect(chat).toContain(phrase);
  }
});

it('채팅 주요 조작 6가지를 번호별 텍스트로 안내하고 발표 중 비노출을 설명한다', async () => {
  await renderHelp();

  const numbered = host.querySelectorAll('ol[aria-label="새미나이 채팅의 번호별 사용방법"] > li');
  expect(numbered).toHaveLength(6);
  for (const label of [
    '채팅 열기',
    '대화 관리',
    '대화 범위',
    '기억 없이 대화',
    '질문 예시',
    '메시지 입력',
  ]) {
    expect([...numbered].some((row) => row.textContent?.includes(label))).toBe(true);
  }
  expect(host.textContent).toContain('장치 점검·발표 연습 중에는 버튼이 보이지 않아요.');
  expect(host.textContent).toContain('오른쪽에 채팅 패널이 열려요.');
  expect(host.textContent).toContain('예시 버튼을 눌러 바로 질문할 수 있어요.');
  expect(host.textContent).toContain('서비스 상태에 따라 달라져요.');
});

it('닫기 버튼이 모달을 닫고 언마운트 시 본문 스크롤을 복원한다', async () => {
  const previousOverflow = document.body.style.overflow;
  await renderHelp();

  const dialog = host.querySelector('dialog')!;
  expect(dialog.open).toBe(true);
  expect(dialog.getAttribute('aria-labelledby')).toBeTruthy();
  expect(document.body.style.overflow).toBe('hidden');

  await act(async () => {
    host
      .querySelector('dialog header button')!
      .dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  expect(dialog.open).toBe(false);
  expect(onDismiss).toHaveBeenCalledOnce();

  await act(async () => root.unmount());
  expect(document.body.style.overflow).toBe(previousOverflow);
});
