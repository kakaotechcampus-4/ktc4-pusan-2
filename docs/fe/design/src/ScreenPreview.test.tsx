/// <reference types="node" />
/** 장치 · API 없이 미리보기의 흐름 · 상태 조작 · 링크 처리와 무대 클래스를 확인한다. */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { screens } from './catalog';
import { ScreenPreview } from './ScreenPreview';

let host: HTMLDivElement;
let root: Root;
let fetchSpy: ReturnType<typeof vi.spyOn>;
let getUserMedia: ReturnType<typeof vi.fn>;
// Vitest는 CSS를 비워서 불러오므로 파일을 직접 읽는다. jsdom의 URL은 fs가 받지 않아 경로 문자열로 만든다
const stageCss = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), 'ui/stage.css'),
  'utf8',
);
const dialogMethods = {
  showModal: Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'showModal'),
  close: Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'close'),
};

/** 미리보기의 이동을 그대로 따라가는 작은 뷰어 */
function Flow({ initial, initialState }: { initial: string; initialState: string }) {
  const [selection, setSelection] = useState({ id: initial, state: initialState });
  return (
    <MemoryRouter>
      <ScreenPreview
        key={selection.id + selection.state}
        screenId={selection.id}
        stateId={selection.state}
        onNavigate={(id, state) => setSelection({ id, state: state ?? 'default' })}
      />
    </MemoryRouter>
  );
}

async function render(initial = 'pitch-create', initialState = 'default') {
  await act(async () => root.render(<Flow initial={initial} initialState={initialState} />));
}

function button(label: string) {
  return [...host.querySelectorAll('button')].find((item) => item.textContent?.trim() === label);
}

async function click(label: string) {
  const target = button(label);
  expect(target, `버튼이 없습니다: ${label}`).toBeDefined();
  await act(async () => target!.click());
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  fetchSpy = vi.spyOn(globalThis, 'fetch');
  getUserMedia = vi.fn();
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia },
  });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  try {
    await act(async () => root.unmount());
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(getUserMedia).not.toHaveBeenCalled();
  } finally {
    host.remove();
    Reflect.deleteProperty(navigator, 'mediaDevices');
    for (const [name, descriptor] of Object.entries(dialogMethods)) {
      if (descriptor) Object.defineProperty(HTMLDialogElement.prototype, name, descriptor);
      else Reflect.deleteProperty(HTMLDialogElement.prototype, name);
    }
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  }
});

it('피치 생성 네 단계를 거쳐 세팅, 무대 종료 확인, 분석 대기로 이동한다', async () => {
  await render();
  await click('다음 · 대본 →');
  expect(host.textContent).toContain('대본 입력');
  await click('대본 매핑');
  expect(host.textContent).toContain('목표 발표시간');
  await click('다음 →');
  // 저장한 평가기준이 없으면 "연습 시작"은 막히고 "건너뛰기"로 시작한다
  expect(button('연습 시작 →')?.disabled).toBe(true);
  await click('건너뛰기');
  await click('Take 3 시작하기');
  expect(host.querySelector('.stage')?.getAttribute('data-script-mode')).toBe('HIGHLIGHT');
  await click('중간 종료');
  expect(host.textContent).toContain('정말 끝낼까요?');
  await click('정말 끝낼까요?');
  expect(host.textContent).toContain('분석 중');
});

it('카메라 · 마이크 · 시선 기준이 모두 돼야 시작할 수 있다', async () => {
  await render('prepare-mission', 'no-camera');
  expect(button('Take 3 시작하기')?.disabled).toBe(true);
  expect(host.textContent).toContain('카메라를 켜야 점검을 시작할 수 있습니다');
  await click('카메라·마이크 켜기');
  expect(button('Take 3 시작하기')?.disabled).toBe(true);
  expect(host.textContent).toContain('시선 기준을 먼저 잡아야');
  const gaze = [...host.querySelectorAll('button')].find((item) =>
    item.textContent?.includes('누르면 시작'),
  )!;
  await act(async () => gaze.click());
  expect(button('Take 3 시작하기')?.disabled).toBe(false);

  await act(async () => root.render(null));
  await render('prepare-mission', 'mic-error');
  expect(button('Take 3 시작하기')?.disabled).toBe(true);
  expect(host.textContent).toContain('마이크에 대고 한 마디 해보세요');
});

it('슬라이드가 없을 때는 다음 단계가 막히고 샘플 파일 선택 뒤 진행할 수 있다', async () => {
  await render('pitch-create', 'empty');
  expect(button('다음 · 대본 →')?.disabled).toBe(true);
  await click('PDF 파일 선택');
  await click('다음 · 대본 →');
  expect(host.textContent).toContain('대본 입력');
});

it('실전 모드를 고르면 대본 칸 없는 실전 무대로 가고 종료 확인에서도 유지한다', async () => {
  await render('prepare-mission');
  const exam = [...host.querySelectorAll('[role="radio"]')].find((item) =>
    item.textContent?.includes('실전 모드'),
  ) as HTMLButtonElement;
  await act(async () => exam.click());
  await click('Take 3 실전 모드로 시작');
  expect(host.querySelector('.stage')?.getAttribute('data-script-mode')).toBe('OFF');
  expect(host.querySelector('.script')).toBeNull();
  await click('중간 종료');
  expect(host.querySelector('.stage')?.getAttribute('data-script-mode')).toBe('OFF');
  await click('정말 끝낼까요?');
  expect(host.textContent).toContain('분석 중');
});

it('개별 보기에서는 슬라이드 미리보기를 페이지를 막는 창으로 열고, 닫으면 생성 화면으로 돌아간다', async () => {
  HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
    this.open = true;
  };
  await render();
  await click('슬라이드 크게 보기');
  expect(host.querySelector('dialog')?.open).toBe(true);
  await click('닫기');
  expect(host.querySelector('dialog')).toBeNull();
  expect(host.textContent).toContain('슬라이드 크게 보기');
});

it('전체 보기(inline)에서는 창을 페이지를 막지 않게 그 자리에 연다', async () => {
  const showModal = vi.fn();
  HTMLDialogElement.prototype.showModal = showModal;
  await act(async () =>
    root.render(
      <MemoryRouter>
        <ScreenPreview screenId="pitch-create" stateId="preview" onNavigate={vi.fn()} inline />
      </MemoryRouter>,
    ),
  );
  expect(host.querySelector('dialog')?.hasAttribute('open')).toBe(true);
  expect(showModal).not.toHaveBeenCalled();
});

it('제품 링크 · 옛 시안 주소를 설계서 안에서 처리하고 가운데 클릭은 막는다', async () => {
  const navigate = vi.fn();
  await act(async () =>
    root.render(
      <MemoryRouter>
        <ScreenPreview screenId="processing-loading" stateId="default" onNavigate={navigate} />
      </MemoryRouter>,
    ),
  );
  const anchor = host.querySelector('a')!;
  anchor.setAttribute('href', '/takes?pitch=sample');
  const modified = new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true });
  await act(async () => {
    anchor.dispatchEvent(modified);
  });
  expect(modified.defaultPrevented).toBe(true);
  expect(navigate).toHaveBeenLastCalledWith('takes');

  const middle = new MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 1 });
  await act(async () => {
    anchor.dispatchEvent(middle);
  });
  expect(middle.defaultPrevented).toBe(true);
  expect(navigate).toHaveBeenCalledTimes(1);

  anchor.setAttribute('href', '/report');
  await act(async () => anchor.click());
  expect(navigate).toHaveBeenLastCalledWith('draft-report');

  anchor.setAttribute('href', '/takes/t1/processing-extra');
  await act(async () => anchor.click());
  expect(navigate).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain('연결하지 않은 화면');
});

it('제품 문구대로 그린다: 재시도의 서버 종료, 리포트의 지표 이름', async () => {
  await render('retry', 'gone');
  expect(host.textContent).toContain('이 Take는 서버에서 이미 끝났어요');
  await act(async () => root.render(null));
  await render('report', 'no-gaze');
  expect(host.textContent).toContain('청중 응시');
  expect(host.textContent).toContain('말하기 속도');
  expect(host.textContent).toContain('이번 Take는 시선을 재지 않았어요');
});

it('무대 미리보기의 클래스는 모두 설계서의 stage.css에 있다', async () => {
  const used = new Set<string>();
  for (const stateId of ['default', 'keyword', 'exam', 'disconnected']) {
    await render('stage', stateId);
    for (const element of host.querySelectorAll('.rehearsal-page, .rehearsal-page *')) {
      for (const name of element.classList) if (name !== 'sr-only') used.add(name);
    }
    await act(async () => root.render(null));
  }
  expect(used.size).toBeGreaterThan(20);
  const missing = [...used].filter((name) => !new RegExp(`\\.${name}(?![\\w-])`).test(stageCss));
  expect(missing).toEqual([]);
});

it('모든 화면 · 상태가 오류 없이 그려지고, 미리보기 안에는 <main>이 없다', async () => {
  const errors = vi.spyOn(console, 'error');
  HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
    this.open = true;
  };
  for (const screen of screens) {
    for (const { id } of screen.states) {
      await render(screen.id, id);
      expect(host.textContent, `${screen.id}:${id}`).not.toBe('');
      expect(host.querySelector('main'), `${screen.id}:${id}`).toBeNull();
      await act(async () => root.render(null));
    }
  }
  expect(errors).not.toHaveBeenCalled();
});
