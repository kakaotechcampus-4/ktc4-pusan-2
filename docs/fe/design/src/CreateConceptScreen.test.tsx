import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { CreateConceptScreen } from './screens/CreateConceptScreen';
import { entries } from './catalog';

let host: HTMLDivElement;
let root: Root;
let fetchSpy: ReturnType<typeof vi.spyOn>;
const navigate = vi.fn();
const methods = {
  showModal: HTMLDialogElement.prototype.showModal,
  close: HTMLDialogElement.prototype.close,
};
const button = (label: string) =>
  [...host.querySelectorAll('button')].find((el) => el.textContent?.trim() === label)!;
const click = async (label: string) => {
  await act(async () => button(label).click());
};
async function render(stateId = 'default', inline = false) {
  await act(async () =>
    root.render(<CreateConceptScreen stateId={stateId} onNavigate={navigate} inline={inline} />),
  );
}
async function finishMapping() {
  await act(async () => vi.advanceTimersByTimeAsync(500));
}
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.useFakeTimers();
  fetchSpy = vi.spyOn(globalThis, 'fetch');
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
    this.dispatchEvent(new Event('close'));
  };
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  expect(fetchSpy).not.toHaveBeenCalled();
  Object.assign(HTMLDialogElement.prototype, methods);
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  navigate.mockReset();
});

it('PM 검토 시안으로 구분하고 기존 제품 샘플도 유지한다', () => {
  expect(entries.find((entry) => entry.id === 'pitch-create-a')).toMatchObject({ status: '시안' });
  expect(entries.find((entry) => entry.id === 'pitch-create')).toMatchObject({
    status: '제품에 있음',
  });
});
it('네 섹션·편차 0/0·접힌 폴더로 시작하고 날짜와 파일 교체는 없다', async () => {
  await render('empty');
  expect(host.querySelectorAll('.create-content > section')).toHaveLength(4);
  expect([...host.querySelectorAll('details')].every((el) => !el.open)).toBe(true);
  expect(host.querySelector<HTMLInputElement>('[aria-label="짧게 허용"]')?.value).toBe('0');
  expect(host.querySelector<HTMLInputElement>('[aria-label="길게 허용"]')?.value).toBe('0');
  expect(host.textContent).not.toMatch(/발표 날짜|파일 교체/);
  await click('PDF 파일 선택');
  expect(host.querySelector('.create-file-summary')?.textContent).toContain('서비스 소개.pdf');
});
it('버전 30개를 읽기 전용으로 열어도 본문과 연습할 조합을 유지한다', async () => {
  await render('many');
  const before = host.querySelector('.create-mapping-summary')!.textContent;
  await act(async () => host.querySelector<HTMLElement>('.create-versions > summary')!.click());
  const rail = host.querySelector('.create-versions')!;
  expect(rail.textContent).not.toMatch(/발표정보|발표 자료|새 버전/);
  for (const [index, kind] of ['슬라이드', '대본', '평가기준'].entries()) {
    await act(async () =>
      rail.querySelectorAll<HTMLElement>('.create-version-tree summary')[index]!.click(),
    );
    const trigger = rail.querySelector<HTMLButtonElement>(`[aria-label="${kind} V30 보기"]`)!;
    trigger.focus();
    await act(async () => trigger.click());
    const dialog = host.querySelector('dialog')!;
    expect(dialog.textContent).toContain(`${kind} V30 · 자료 보기`);
    expect(dialog.querySelector('textarea, input')).toBeNull();
    await click('닫기');
    expect(document.activeElement).toBe(trigger);
    expect(host.querySelector('.create-mapping-summary')!.textContent).toBe(before);
  }
  await click('시작 전 점검 →');
  expect(navigate).toHaveBeenCalledWith('prepare-mission');
});
it('파일명 없는 연결 메뉴에서 키보드로 선택하고 예시 매핑 완료 때만 창을 연다', async () => {
  await render('mapping-error');
  expect(host.querySelector('dialog')).toBeNull();
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('입력한 대본은 그대로');
  const trigger = host.querySelector<HTMLButtonElement>('[aria-haspopup="menu"]')!;
  await act(async () => trigger.click());
  const options = host.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]');
  expect(host.querySelector('[role="menu"]')?.textContent).not.toContain('.pdf');
  expect(document.activeElement).toBe(options[0]);
  await act(async () =>
    options[0]!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })),
  );
  expect(document.activeElement).toBe(options[1]);
  await act(async () => options[1]!.click());
  expect(document.activeElement).toBe(trigger);
  await click('대본 매핑');
  expect(host.querySelector('dialog')).toBeNull();
  await finishMapping();
  expect(host.querySelector('dialog')?.textContent).toContain('슬라이드 V2');
  expect(host.querySelector('[aria-haspopup="menu"]')).toBeNull();
});
it('매핑 편집값은 닫고 다시 열어도 남고 재매핑·저장 후에만 준비 완료가 된다', async () => {
  await render('ready');
  await click('매핑 확인/수정');
  await click('다음 장');
  const field = host.querySelector<HTMLTextAreaElement>('dialog textarea')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(
      field,
      '수정한 둘째 장',
    );
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });
  expect(button('저장').disabled).toBe(true);
  await click('닫기');
  expect(button('시작 전 점검 →')).toBeUndefined();
  await click('매핑 확인/수정');
  await click('다음 장');
  expect(host.querySelector<HTMLTextAreaElement>('dialog textarea')?.value).toBe('수정한 둘째 장');
  await click('수정 대본 매핑');
  await finishMapping();
  await click('저장');
  await click('닫기');
  expect(button('시작 전 점검 →').disabled).toBe(false);
});
it('전체 보기에서는 모달이 다른 시안을 가리지 않는다', async () => {
  const show = vi.spyOn(HTMLDialogElement.prototype, 'showModal');
  await render('mapping', true);
  expect(host.querySelector('dialog')?.open).toBe(true);
  expect(show).not.toHaveBeenCalled();
  expect(document.body.style.overflow).not.toBe('hidden');
});
