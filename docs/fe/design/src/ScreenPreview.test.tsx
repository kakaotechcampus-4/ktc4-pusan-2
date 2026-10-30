/// <reference types="node" />
/** 장치 · API 없이 미리보기의 흐름 · 상태 조작 · 링크 처리와 무대 클래스를 확인한다. */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { entries } from './catalog';
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

/** 마지막 장까지 → 키로 넘긴다(무대 샘플은 3번째 장에서 시작) */
async function toLastSlide() {
  await act(async () => {
    for (let i = 0; i < 12; i++)
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }));
  });
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
  // 마지막 장이 아니면 끝낼 수 없다(#697)
  expect(button('발표 끝내기')?.disabled).toBe(true);
  expect(button('발표 끝내기')?.getAttribute('aria-describedby')).toBe('end-hint');
  expect(host.querySelector('#end-hint')?.textContent).toBe('마지막 장에서 끝낼 수 있어요');
  await toLastSlide();
  expect(host.textContent).toContain('SLIDE 12 / 12');
  await click('발표 끝내기');
  expect(host.textContent).toContain('정말 끝낼까요?');
  await click('정말 끝낼까요?');
  expect(host.textContent).toContain('분석 중');
});

it('홈 기본 상태는 유지하고 시안의 발표 준비 버튼은 각 제목과 같은 경로와 인자로 이동한다', async () => {
  await render('home');
  expect(host.querySelector('a[aria-label$=" 발표 준비"]')).toBeNull();
  await act(async () => root.render(null));
  const onNavigate = vi.fn();
  await act(async () =>
    root.render(
      <MemoryRouter>
        <ScreenPreview screenId="home" stateId="prepare-entry-draft" onNavigate={onNavigate} />
      </MemoryRouter>,
    ),
  );
  const rows = [...host.querySelectorAll('section[aria-label="발표 목록"] li')];
  expect(rows).toHaveLength(2);
  for (const row of rows) {
    expect(row.querySelector('.w-28.shrink-0')?.textContent).toContain('Take 기록');
    expect(row.querySelector('.w-20.shrink-0')).not.toBeNull();
    expect(row.lastElementChild?.textContent?.trim()).toBe('발표 준비');
    const title = row.querySelector('a')!;
    const prepare = row.lastElementChild as HTMLAnchorElement;
    expect(prepare.getAttribute('href')).toBe(title.getAttribute('href'));
    expect(prepare.getAttribute('href')).toMatch(/\/edit$/);
    await act(async () => title.click());
    expect(onNavigate).toHaveBeenLastCalledWith('pitch-create');
    const titleArguments = onNavigate.mock.calls.at(-1);
    await act(async () => prepare.click());
    expect(onNavigate.mock.calls.at(-1)).toEqual(titleArguments);
  }
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

it('슬라이드가 없을 때는 다음 단계 버튼이 없고 샘플 파일 선택 뒤 진행할 수 있다', async () => {
  await render('pitch-create', 'empty');
  expect(button('다음 · 대본 →')).toBeUndefined();
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
  // 마지막 장이 아니면 끝낼 수 없다(#697)
  expect(button('발표 끝내기')?.disabled).toBe(true);
  expect(button('발표 끝내기')?.getAttribute('aria-describedby')).toBe('end-hint');
  expect(host.querySelector('#end-hint')?.textContent).toBe('마지막 장에서 끝낼 수 있어요');
  await toLastSlide();
  expect(host.textContent).toContain('SLIDE 12 / 12');
  await click('발표 끝내기');
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

  anchor.setAttribute('href', '/about');
  await act(async () => anchor.click());
  expect(navigate).toHaveBeenLastCalledWith('about');

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

it('모든 기초 · 화면 · 상태가 오류 없이 그려지고, 미리보기 안에는 <main>이 없다', async () => {
  const errors = vi.spyOn(console, 'error');
  HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
    this.open = false;
  };
  HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
    this.open = true;
  };
  for (const screen of entries) {
    for (const { id } of screen.states) {
      await render(screen.id, id);
      expect(host.textContent, `${screen.id}:${id}`).not.toBe('');
      expect(host.querySelector('main'), `${screen.id}:${id}`).toBeNull();
      await act(async () => root.render(null));
    }
  }
  expect(errors).not.toHaveBeenCalled();
});

it('안내 화면: 미지원 사유는 상태마다 제품 문구로, 로고 · 버튼은 홈으로 간다', async () => {
  await render('unsupported', 'mobile');
  expect(host.textContent).toContain('발표 연습은 PC에서 해 주세요');
  await act(async () => root.render(null));
  await render('unsupported', 'browser');
  expect(host.textContent).toContain('이 브라우저에서는 시선 분석을 할 수 없어요');
  await click('내 피치로');
  expect(host.textContent).toContain('새 피치 만들기');
  await act(async () => root.render(null));
  await render('about');
  await act(async () =>
    host.querySelector<HTMLButtonElement>('[aria-label="피치코치 홈"]')!.click(),
  );
  expect(host.textContent).toContain('새 피치 만들기');
});

it('컴포넌트 · 대화 상자 견본: 종류가 다 보이고, 창은 상태대로 막히며 닫으면 다시 열 수 있다', async () => {
  HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
    this.open = false;
  };
  HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
    this.open = true;
  };
  await render('button', 'variants');
  for (const label of ['primary', 'secondary', 'quiet', 'ButtonLink'])
    expect(host.textContent).toContain(label);
  await act(async () => root.render(null));

  await render('confirm-dialog', 'busy');
  expect(button('끝내는 중…')?.disabled).toBe(true);
  expect(button('취소')?.disabled).toBe(true);
  await act(async () => root.render(null));

  await render('survey-dialog', 'open');
  expect(button('설문 보내기')?.disabled).toBe(true);
  await click('나중에');
  expect(button('설문 보내기')).toBeUndefined();
  await click('설문 창 다시 열기');
  expect(button('설문 보내기')).toBeDefined();
  await act(async () => root.render(null));

  await render('survey-dialog', 'filled');
  expect(button('설문 보내기')?.disabled).toBe(false);
  await act(async () => root.render(null));

  await render('consent-dialog', 'open');
  expect(host.querySelector('dialog')?.textContent).toContain('음성 · 영상 원본은');
  expect(button('동의하고 참여하기')).toBeDefined();
});

it('설문(#776): 홈은 참여 안내 · 남은 설문만 두고, 설문은 리포트 아래에서 보낸다', async () => {
  HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
    this.open = false;
  };
  HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
    this.open = true;
  };
  await render('home');
  expect(host.textContent).not.toContain('사용자 테스트에 참여해 주세요');
  expect(host.textContent).not.toContain('설문을 기다리는');
  await act(async () => root.render(null));

  await render('home', 'consent');
  await click('안내 보고 참여하기');
  expect(button('동의하고 참여하기')).toBeDefined();
  await act(async () => root.render(null));

  await render('home', 'pending');
  expect(host.textContent).toContain('설문을 기다리는 리포트가 2개 있어요');
  await act(async () => root.render(null));

  await render('report');
  await click('이번 분석 설문 남기기');
  expect(button('설문 보내기')?.disabled).toBe(true);
  await act(async () => root.render(null));

  await render('report', 'survey-sent');
  expect(button('이번 분석 설문 남기기')).toBeUndefined();
  expect(host.textContent).toContain('설문을 보냈어요');
});

it('공용 상단바는 제품과 같은 화면에만 얹히고, 지금 메뉴를 표시한다', async () => {
  const current = () => host.querySelector('nav[aria-label="홈 메뉴"] [aria-current="page"]');
  await render('home');
  expect(current()?.textContent).toBe('내 피치');
  await act(async () => root.render(null));
  await render('pitch-create');
  expect(host.querySelector('nav[aria-label="홈 메뉴"]')).toBeTruthy();
  expect(current()).toBeNull();
  // 경로 줄은 상단바의 "내 피치"가 대신한다(#738)
  expect(host.querySelector('nav[aria-label="경로"]')).toBeNull();
  await act(async () => root.render(null));
  await render('stage');
  expect(host.querySelector('nav[aria-label="홈 메뉴"]')).toBeNull();
  await act(async () => root.render(null));
  // 시작 전 세팅은 상단바 없이 자체 틀을 쓴다(#738 최종, #706에서 정함)
  await render('prepare-mission');
  expect(host.querySelector('nav[aria-label="홈 메뉴"]')).toBeNull();
});

it('끝내기 확인은 장이 바뀌면 풀리고, 마지막 장에서는 안내 글자가 없다', async () => {
  await render('stage', 'last-slide');
  expect(host.querySelector('#end-hint')).toBeNull();
  expect(button('발표 끝내기')?.getAttribute('aria-describedby')).toBeNull();
  await click('발표 끝내기');
  expect(button('정말 끝낼까요?')).toBeDefined();
  // 이전 장에 갔다 돌아오면 처음부터 다시 묻는다(제품 useEndConfirm)
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }));
  });
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }));
  });
  expect(host.textContent).toContain('SLIDE 12 / 12');
  expect(button('정말 끝낼까요?')).toBeUndefined();
  expect(button('발표 끝내기')?.disabled).toBe(false);
});

it('무대 시작 전 안내: 막힌 경우에만 다시 시도 · 장치 점검으로를 두고, 시작 전에는 끝낼 수 없다', async () => {
  await render('stage', 'opening');
  expect(host.textContent).toContain('카메라와 마이크를 여는 중이에요');
  expect(button('다시 시도')).toBeUndefined();
  await act(async () => root.render(null));
  await render('stage', 'busy');
  expect(button('다시 시도')).toBeDefined();
  await toLastSlide();
  expect(button('발표 끝내기')?.disabled).toBe(true);
  await click('장치 점검으로');
  expect(host.textContent).toContain('점검 항목');
});
