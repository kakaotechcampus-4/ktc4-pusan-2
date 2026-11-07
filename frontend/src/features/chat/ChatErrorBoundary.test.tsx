// @vitest-environment jsdom
import { act, useState, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter, Outlet, RouterProvider } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ChatAgentHost } from './ChatAgentHost';
import { CHAT_FAILED_TEXT, ChatErrorBoundary } from './ChatErrorBoundary';

const state = vi.hoisted(() => ({
  /** 대화창이 그릴 때마다 던집니다 — 같은 데이터 때문에 되풀이되는 오류 */
  alwaysThrow: false,
  renders: 0,
  sessionThrows: false,
}));
vi.mock('@/shared/api/useSession', () => ({
  useSession: () => {
    if (state.sessionThrows) throw new Error('session boom');
    return { isPending: false, isError: false, data: { id: 'u1' } };
  },
}));

let host: HTMLDivElement;
let root: Root;
let router: ReturnType<typeof createMemoryRouter>;
let pageClicks: number;
const resets: number[] = [];
// React 는 잡은 오류도 콘솔 · window error 로 알립니다. 시험 출력만 조용히 합니다
const silence = (event: ErrorEvent) => event.preventDefault();

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubEnv('VITE_CHAT_AGENT', 'true');
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  window.addEventListener('error', silence);
  state.alwaysThrow = false;
  state.renders = 0;
  state.sessionThrows = false;
  pageClicks = 0;
  resets.length = 0;
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  router.dispose();
  host.remove();
  window.removeEventListener('error', silence);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

const countRender = () => {
  state.renders++;
};

/** 대화창 대역 — 런처와, 열면 입력칸과 [터뜨리기]가 있습니다 */
function FakeChat() {
  const [open, setOpen] = useState(false);
  const [crash, setCrash] = useState(false);
  countRender();
  if (crash || state.alwaysThrow) throw new Error('chat boom');
  return (
    <div data-chat-launcher="">
      {open && (
        <section>
          <input aria-label="대화 입력" />
          <button type="button" onClick={() => setCrash(true)}>
            터뜨리기
          </button>
        </section>
      )}
      <button type="button" aria-haspopup="dialog" onClick={() => setOpen(true)}>
        런처
      </button>
    </div>
  );
}

async function visit(path: string, chat: ReactNode) {
  router = createMemoryRouter(
    [
      {
        element: (
          <>
            <Outlet />
            {chat}
          </>
        ),
        children: [
          {
            path: '/takes/:takeId',
            element: (
              <button type="button" onClick={() => pageClicks++}>
                리포트 버튼
              </button>
            ),
          },
          { path: '/takes/:takeId/rehearsal', element: <p>리허설</p> },
        ],
      },
    ],
    { initialEntries: [path] },
  );
  await act(async () => root.render(<RouterProvider router={router} />));
}

const visitFake = (path = '/takes/t1') =>
  visit(
    path,
    <ChatErrorBoundary onReset={(failures) => resets.push(failures)}>
      <FakeChat />
    </ChatErrorBoundary>,
  );

const button = (text: string) =>
  [...host.querySelectorAll('button')].find((b) => b.textContent === text) ?? null;
const byLabel = (label: string) => host.querySelector<HTMLElement>(`[aria-label="${label}"]`);
const alerts = () => host.querySelectorAll('[role="alert"]');
const click = (el: HTMLElement) => act(async () => el.click());

async function crashInsidePanel() {
  await click(button('런처')!);
  byLabel('대화 입력')!.focus();
  await click(button('터뜨리기')!);
}

it('대화창이 던지면 런처 자리에 안내와 [다시 시도]를 한 번 알리고, 바깥 페이지는 그대로 쓴다', async () => {
  await visitFake();
  await crashInsidePanel();

  expect(button('런처')).toBeNull();
  expect(alerts()).toHaveLength(1);
  expect(alerts()[0].textContent).toBe(CHAT_FAILED_TEXT);
  expect(button('다시 시도')).not.toBeNull();
  // 장치 점검의 오른쪽 여백이 그대로 유지되도록 런처와 같은 표시를 둡니다
  expect(host.querySelector('[data-chat-launcher] [role="alert"]')).not.toBeNull();

  await click(button('리포트 버튼')!);
  expect(pageClicks).toBe(1);
  // 다시 그려도 안내는 하나뿐입니다
  await act(async () => router.navigate('/takes/t2'));
  expect(alerts()).toHaveLength(1);
});

it('대화창 안에 초점이 있었으면 [다시 시도]로 옮기고, 바깥 페이지에 있었으면 그대로 둔다', async () => {
  await visitFake();
  await crashInsidePanel();
  expect(document.activeElement).toBe(button('다시 시도'));

  await act(async () => root.unmount());
  root = createRoot(host);
  await visitFake();
  await click(button('런처')!);
  button('리포트 버튼')!.focus();
  await click(button('터뜨리기')!);
  expect(document.activeElement).toBe(button('리포트 버튼'));
});

it('[다시 시도]를 누르면 저장소 정리를 부르고 닫힌 대화창을 다시 그리며 초점을 런처로 옮긴다', async () => {
  await visitFake();
  await crashInsidePanel();
  const rendersBefore = state.renders;

  await click(button('다시 시도')!);
  expect(resets).toEqual([1]);
  expect(state.renders).toBeGreaterThan(rendersBefore);
  expect(alerts()).toHaveLength(0);
  expect(button('런처')).not.toBeNull();
  // 대화만 닫힌 채로 돌아옵니다
  expect(byLabel('대화 입력')).toBeNull();
  expect(document.activeElement).toBe(button('런처'));
});

it('같은 오류가 되풀이돼도 누를 때만 다시 그리고, 안내를 다시 알리며 초점은 [다시 시도]에 둔다', async () => {
  await visitFake();
  await crashInsidePanel();
  state.alwaysThrow = true;

  await click(button('다시 시도')!);
  const afterOneRetry = state.renders;
  expect(alerts()).toHaveLength(1);
  expect(document.activeElement).toBe(button('다시 시도'));

  // 누르지 않으면 다시 그리지 않습니다
  await act(async () => router.navigate('/takes/t2'));
  await act(async () => new Promise((resolve) => setTimeout(resolve, 20)));
  expect(state.renders).toBe(afterOneRetry);

  await click(button('다시 시도')!);
  expect(resets).toEqual([1, 2]);
  expect(alerts()).toHaveLength(1);
});

it('[닫기]는 안내를 런처 크기의 [다시 시도]로 접고 초점을 그리로 옮긴다', async () => {
  await visitFake();
  await crashInsidePanel();

  await click(button('닫기')!);
  expect(alerts()).toHaveLength(0);
  const folded = byLabel('대화창 다시 시도')!;
  expect(folded.title).toBe(CHAT_FAILED_TEXT);
  expect(document.activeElement).toBe(folded);

  await click(folded);
  expect(resets).toEqual([1]);
  expect(button('런처')).not.toBeNull();
  expect(document.activeElement).toBe(button('런처'));
});

it('채팅이 숨는 화면에서는 안내를 그리지 않는다', async () => {
  await visitFake();
  await crashInsidePanel();
  await act(async () => router.navigate('/takes/t1/rehearsal'));
  expect(host.querySelector('[data-chat-launcher]')).toBeNull();
  await act(async () => router.navigate('/takes/t1'));
  expect(alerts()).toHaveLength(1);
});

it('실제 채팅 호스트가 렌더 중에 던져도 리포트 버튼은 그대로 쓰고, 다시 시도하면 런처가 돌아온다', async () => {
  state.sessionThrows = true;
  await visit('/takes/t1', <ChatAgentHost />);
  expect(alerts()[0]?.textContent).toBe(CHAT_FAILED_TEXT);
  await click(button('리포트 버튼')!);
  expect(pageClicks).toBe(1);

  state.sessionThrows = false;
  await click(button('다시 시도')!);
  expect(byLabel('채팅 에이전트')).not.toBeNull();
  expect(alerts()).toHaveLength(0);
});
