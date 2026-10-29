/**
 * 화면 설계서 뷰어. 화면 목록은 catalog, 미리보기는 PreviewFrame(iframe 안의 ScreenPreview)이 맡는다.
 * 고른 화면 · 상태 · 보기 방식 · 미리보기 크기는 주소(`#/?screen=&state=&view=&device=`)에 두어
 * 새로고침 · 뒤로 가기에서 이어진다. 옛 시안 주소(`#/report` 등)는 그 시안 항목으로 바꿔 연다.
 */
import { useEffect, useRef, useState, type RefObject } from 'react';
import { Navigate, Route, Routes, useParams, useSearchParams } from 'react-router';
import { Button } from '@/ui/Button';
import { PRODUCT_ORIGIN, screens } from './catalog';
import { FramePage } from './FramePage';
import { DEVICES, FRAME_UPDATED_EVENT, frameUrl, isDevice, type Device } from './frame';
import { PreviewFrame } from './PreviewFrame';
import type { GuideScreen } from './model';

const control =
  'min-h-11 w-full border-2 border-ink/60 bg-panel px-3 py-2 text-left text-sm text-ink';

type View = 'single' | 'all';

/** 설계서의 주소 표. main.tsx와 테스트가 같이 쓴다 */
export function DesignRoutes() {
  return (
    <Routes>
      <Route path="/" element={<DesignGuide />} />
      <Route path="/frame" element={<FramePage />} />
      <Route path="/:name" element={<LegacyDraftRoute />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

/** 옛 시안 주소 `/:name`을 뷰어의 시안 항목으로 바꾼다. 없는 이름이면 기본 화면 */
function LegacyDraftRoute() {
  const { name = '' } = useParams();
  const id = `draft-${name}`;
  const to = screens.some((screen) => screen.id === id)
    ? `/?${new URLSearchParams({ screen: id })}`
    : '/';
  return <Navigate to={to} replace />;
}

/** 그 화면에 있는 상태 ID면 그대로, 아니면 undefined */
function validState(screen: GuideScreen, id: string | null | undefined) {
  return screen.states.find((state) => state.id === id)?.id;
}

function tokenName(item: string) {
  return item.startsWith('script-') ? `--spacing-${item}` : `--color-${item}`;
}

/** 키 순서와 상관없이 같은 주소인지 */
function sameParams(a: URLSearchParams, b: URLSearchParams) {
  const [x, y] = [new URLSearchParams(a), new URLSearchParams(b)];
  x.sort();
  y.sort();
  return x.toString() === y.toString();
}

function Navigation({
  currentId,
  onSelect,
  query,
  onQuery,
}: {
  currentId: string | undefined;
  onSelect: (id: string) => void;
  query: string;
  onQuery: (value: string) => void;
}) {
  const filtered = screens.filter((screen) =>
    `${screen.title} ${screen.id} ${screen.group} ${screen.summary} ${screen.route}`
      .toLocaleLowerCase()
      .includes(query.trim().toLocaleLowerCase()),
  );
  const groups = [...new Set(filtered.map((screen) => screen.group))];

  return (
    <div className="flex flex-col gap-5">
      <label className="flex flex-col gap-2 text-sm font-bold">
        화면 검색
        <input
          className={control}
          type="search"
          placeholder="화면 이름이나 경로 검색"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
        />
      </label>
      <nav aria-label="화면 목록" className="flex flex-col gap-5">
        {groups.map((group) => (
          <section key={group}>
            <h2 className="mb-2 text-xs font-bold text-ink/80">{group}</h2>
            <ul className="flex flex-col gap-1">
              {filtered
                .filter((screen) => screen.group === group)
                .map((screen) => (
                  <li key={screen.id}>
                    <button
                      type="button"
                      aria-current={currentId === screen.id ? 'page' : undefined}
                      onClick={() => onSelect(screen.id)}
                      className={`min-h-11 w-full px-3 py-2 text-left text-sm font-bold hover:bg-cream ${
                        currentId === screen.id ? 'border-l-4 border-coral-deep bg-coral-wash' : ''
                      }`}
                    >
                      {screen.title}
                    </button>
                  </li>
                ))}
            </ul>
          </section>
        ))}
        {filtered.length === 0 && <p role="status">검색한 화면이 없습니다.</p>}
      </nav>
    </div>
  );
}

function List({ title, items }: { title: string; items: string[] }) {
  return (
    <section className="border-t border-line pt-4">
      <h3 className="mb-2 text-sm font-bold">{title}</h3>
      {items.length > 0 ? (
        <ul className="flex flex-col gap-2 text-sm leading-relaxed">
          {items.map((item) => (
            <li key={item} className="break-words">
              {item}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-ink/80">기록된 항목이 없습니다.</p>
      )}
    </section>
  );
}

/** 화면이 쓰는 디자인 값. 처음 그릴 때와 개발 서버가 CSS를 바꿔 끼운 뒤(뷰어 · 미리보기 iframe 모두)에 읽는다 */
function TokenList({ items, preview }: { items: string[]; preview: RefObject<HTMLIFrameElement> }) {
  const container = useRef<HTMLElement>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  useEffect(() => {
    function refresh() {
      if (!container.current) return;
      // 밝은 지면(.pitch-create)은 같은 이름의 색을 다시 정의하므로, 미리보기에 있으면 그 범위에서 읽는다
      const light = preview.current?.contentDocument?.querySelector('.pitch-create');
      const styles = light
        ? light.ownerDocument.defaultView!.getComputedStyle(light)
        : getComputedStyle(container.current);
      setValues(
        Object.fromEntries(
          items.map((item) => [item, styles.getPropertyValue(tokenName(item)).trim()]),
        ),
      );
    }
    refresh();
    window.addEventListener(FRAME_UPDATED_EVENT, refresh);
    const hot = import.meta.hot;
    hot?.on('vite:afterUpdate', refresh);
    return () => {
      window.removeEventListener(FRAME_UPDATED_EVENT, refresh);
      // Vitest가 넣는 hot 대역에는 off가 없다
      hot?.off?.('vite:afterUpdate', refresh);
    };
  }, [items, preview]);

  return (
    <section ref={container} className="border-t border-line pt-4">
      <h3 className="mb-2 text-sm font-bold">디자인 값</h3>
      <p className="mb-2 text-xs text-ink/80">
        설계서 src/styles.css의 값이다. 미리보기에 밝은 지면이 있으면 그 범위의 값을 보여 준다.
      </p>
      <ul className="flex flex-col gap-2 text-sm">
        {items.map((item) => (
          <li key={item} className="break-all">
            <code>{tokenName(item)}</code>
            {values[item] ? `: ${values[item]}` : ''}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Details({
  screen,
  stateId,
  preview,
}: {
  screen: GuideScreen;
  stateId: string;
  preview: RefObject<HTMLIFrameElement>;
}) {
  const state = screen.states.find((item) => item.id === stateId);
  const links = screen.productLinks ?? [];
  return (
    <aside aria-label="화면 설명" className="flex min-w-0 flex-col gap-4 bg-panel p-5">
      <div>
        <p className="text-xs font-bold text-ink/80">화면 설명</p>
        <h2 className="mt-1 text-lg font-bold">{screen.title}</h2>
        <p className="mt-2 text-sm leading-relaxed">{screen.summary}</p>
        <p className="mt-2 text-sm font-bold">{screen.status}</p>
      </div>
      {state && (
        <section className="border-t border-line pt-4">
          <h3 className="text-sm font-bold">현재 상태: {state.label}</h3>
          <p className="mt-2 text-sm leading-relaxed">{state.description}</p>
        </section>
      )}
      <List title="동작 규칙" items={screen.rules} />
      {screen.productNote && (
        <section className="border-t border-line pt-4">
          <h3 className="text-sm font-bold">제품과 다른 점</h3>
          <p className="mt-2 text-sm leading-relaxed">{screen.productNote}</p>
        </section>
      )}
      {links.length > 0 && (
        <section className="border-t border-line pt-4">
          <h3 className="mb-2 text-sm font-bold">제품에서 열기</h3>
          <ul className="flex flex-col gap-2 text-sm">
            {links.map((item) => (
              <li key={item.path}>
                <a
                  href={`${PRODUCT_ORIGIN}${item.path}`}
                  target="_blank"
                  rel="noreferrer"
                  className="font-bold underline hover:text-coral-deep"
                >
                  {item.label} ↗
                </a>
                {item.note && <span className="block text-xs text-ink/80">{item.note}</span>}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs leading-relaxed text-ink/80">
            제품 개발 서버(frontend/에서 npm run dev)가 떠 있어야 열린다. 로그인이 필요하면 그 탭의
            브라우저 콘솔에 mockSession.on()을 입력한다.
          </p>
        </section>
      )}
      <List title="사용하는 컴포넌트" items={screen.components} />
      <TokenList items={screen.tokens} preview={preview} />
      <List title="관련 코드" items={screen.sources} />
      <section className="border-t border-line pt-4">
        <h3 className="text-sm font-bold">
          {screen.status === '시안만' ? '옛 시안 주소' : '제품 경로'}
        </h3>
        <p className="mt-2 break-all font-mono text-xs">
          {screen.status === '시안만' ? `#${screen.route}` : screen.route}
        </p>
      </section>
    </aside>
  );
}

function StateSelect({
  screen,
  value,
  onChange,
}: {
  screen: GuideScreen;
  value: string;
  onChange: (stateId: string) => void;
}) {
  if (screen.states.length === 0) return null;
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs font-bold sm:min-w-44">
      화면 상태
      <select className={control} value={value} onChange={(event) => onChange(event.target.value)}>
        {screen.states.map((state) => (
          <option key={state.id} value={state.id}>
            {state.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/** 미리보기를 어떤 화면 크기로 그릴지. 칸보다 넓으면 줄여 보인다 */
function DeviceSelect({ value, onChange }: { value: Device; onChange: (device: Device) => void }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs font-bold sm:min-w-44">
      미리보기 크기
      <select
        className={control}
        value={value}
        onChange={(event) => {
          if (isDevice(event.target.value)) onChange(event.target.value);
        }}
      >
        {Object.entries(DEVICES).map(([id, device]) => (
          <option key={id} value={id}>
            {device.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function DesignGuide() {
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState('');
  const mobileNavigation = useRef<HTMLDetailsElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const preview = useRef<HTMLIFrameElement>(null);
  // 다음 주소 변경 때 제목으로 포커스를 옮길지. 실제로 주소를 바꿀 때만 채운다
  const pendingFocus = useRef<'move' | 'keep' | null>(null);

  const screenId = params.get('screen') ?? screens[0]?.id;
  const selected = screens.find((screen) => screen.id === screenId);
  const view: View = params.get('view') === 'all' ? 'all' : 'single';
  const deviceParam = params.get('device');
  const device: Device = isDevice(deviceParam) ? deviceParam : 'fit';
  // 고른 화면은 state, 나머지 카드는 state.<화면 ID>에 상태를 둔다
  const selectedState = selected
    ? (validState(selected, params.get('state')) ??
      validState(selected, params.get(`state.${selected.id}`)) ??
      selected.states[0]?.id ??
      '')
    : '';
  const cardState = (screen: GuideScreen) =>
    screen.id === selected?.id
      ? selectedState
      : (validState(screen, params.get(`state.${screen.id}`)) ?? screen.states[0]?.id ?? '');

  // 화면 · 보기가 바뀌면 제목으로 포커스를 옮긴다. 상태만 바꾼 조작은 그 자리에 둔다.
  // 뒤로 가기처럼 이 화면이 바꾸지 않은 주소 변경은 화면 · 보기가 바뀌었는지로 정한다
  const previous = useRef({ screenId, view });
  useEffect(() => {
    const changed = previous.current.screenId !== screenId || previous.current.view !== view;
    const pending = pendingFocus.current;
    pendingFocus.current = null;
    previous.current = { screenId, view };
    if (pending === 'move' || (pending === null && changed)) heading.current?.focus();
  }, [params, screenId, view]);

  /** 주소를 바꾼다. 같은 주소면 기록을 쌓지 않고 건너뛴다 */
  function commit(next: URLSearchParams, replace: boolean, focus: 'move' | 'keep') {
    if (sameParams(next, params)) return;
    pendingFocus.current = focus;
    setParams(next, { replace });
  }

  /** 다른 화면을 개별 보기로 연다. 뒤로 가기로 돌아올 수 있게 기록을 쌓는다 */
  function openScreen(id: string, stateId?: string) {
    const next = new URLSearchParams(params);
    // 전체 보기에서 떠나는 카드의 상태를 남겨, 돌아왔을 때 그대로 보이게 한다
    if (view === 'all' && selected && selected.id !== id)
      next.set(`state.${selected.id}`, selectedState);
    const target = screens.find((screen) => screen.id === id);
    const state = target && (validState(target, stateId) ?? target.states[0]?.id);
    next.set('screen', id);
    next.set('view', 'single');
    if (state) next.set('state', state);
    else next.delete('state');
    next.delete(`state.${id}`);
    commit(next, false, 'move');
  }

  /** 같은 화면 안의 상태 전환. 보기와 고른 화면은 그대로 두고 기록을 바꿔 끼운다 */
  function changeState(id: string, stateId: string) {
    const next = new URLSearchParams(params);
    if (id === selected?.id) next.set('state', stateId);
    else next.set(`state.${id}`, stateId);
    commit(next, true, 'keep');
  }

  /** 미리보기 안의 버튼 · 링크. 같은 화면이면 상태만, 다른 화면이면 그 화면을 개별 보기로 */
  function previewNavigate(source: GuideScreen, id: string, stateId?: string) {
    if (id === source.id)
      changeState(id, validState(source, stateId) ?? source.states[0]?.id ?? '');
    else openScreen(id, stateId);
  }

  function changeView(nextView: View) {
    const next = new URLSearchParams(params);
    next.set('view', nextView);
    commit(next, false, 'move');
  }

  /** 미리보기 크기는 보는 방식이라 기록을 쌓지 않는다 */
  function changeDevice(nextDevice: Device) {
    const next = new URLSearchParams(params);
    if (nextDevice === 'fit') next.delete('device');
    else next.set('device', nextDevice);
    commit(next, true, 'keep');
  }

  const navigation = (
    <Navigation
      currentId={selected?.id}
      query={query}
      onQuery={setQuery}
      onSelect={(id) => {
        if (mobileNavigation.current) mobileNavigation.current.open = false;
        openScreen(id);
      }}
    />
  );

  return (
    <div className="min-h-dvh bg-greige text-ink">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b-2 border-ink bg-panel px-5 py-4">
        <div>
          <p className="text-xs font-bold text-ink/80">PITCHCOACH</p>
          <h1 className="text-xl font-bold">화면 설계서</h1>
        </div>
        <p className="text-sm">샘플 데이터로 화면과 흐름을 확인합니다.</p>
      </header>
      <div className="grid min-w-0 grid-cols-1 lg:grid-cols-[15rem_minmax(0,1fr)]">
        <aside className="hidden min-w-0 self-start border-r border-line-strong bg-panel p-4 lg:sticky lg:top-0 lg:block lg:max-h-dvh lg:overflow-y-auto">
          {navigation}
        </aside>
        <details
          ref={mobileNavigation}
          className="min-w-0 border-b border-line-strong bg-panel p-4 lg:hidden"
        >
          <summary className="min-h-11 cursor-pointer py-2 font-bold">화면 목록 열기</summary>
          {navigation}
        </details>
        <main className="min-w-0">
          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-line-strong px-5 py-4">
            <div>
              <h2 ref={heading} tabIndex={-1} className="text-lg font-bold">
                {view === 'all' ? '전체 화면' : (selected?.title ?? '화면을 찾을 수 없음')}
              </h2>
              <p className="mt-1 text-sm text-ink/80">
                {view === 'all'
                  ? '각 화면의 상태를 바꿔 비교하세요. 화면별 선택은 주소에 저장됩니다.'
                  : '화면과 오른쪽 설명을 함께 확인하세요.'}
              </p>
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <DeviceSelect value={device} onChange={changeDevice} />
              <div className="flex flex-wrap gap-2" role="group" aria-label="보기 방식">
                <Button
                  size="sm"
                  variant={view === 'single' ? 'primary' : 'secondary'}
                  aria-pressed={view === 'single'}
                  onClick={() => changeView('single')}
                >
                  개별 화면
                </Button>
                <Button
                  size="sm"
                  variant={view === 'all' ? 'primary' : 'secondary'}
                  aria-pressed={view === 'all'}
                  onClick={() => changeView('all')}
                >
                  전체 화면
                </Button>
              </div>
            </div>
          </div>
          {view === 'single' && selected && (
            <div className="grid min-w-0 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_19rem]">
              <div className="min-w-0 p-4 sm:p-5">
                <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
                  <StateSelect
                    screen={selected}
                    value={selectedState}
                    onChange={(id) => changeState(selected.id, id)}
                  />
                  <span className="flex flex-wrap items-center gap-3 text-sm font-bold">
                    {selected.status}
                    <a
                      href={frameUrl(selected.id, selectedState)}
                      target="_blank"
                      rel="noreferrer"
                      className="underline hover:text-coral-deep"
                    >
                      새 탭에서 화면만 보기 ↗
                    </a>
                  </span>
                </div>
                <div
                  role="region"
                  aria-label={`${selected.title} 미리보기`}
                  className="min-w-0 border-2 border-line-strong bg-panel"
                >
                  <PreviewFrame
                    frameRef={preview}
                    screenId={selected.id}
                    stateId={selectedState}
                    onNavigate={(id, stateId) => previewNavigate(selected, id, stateId)}
                    device={device}
                    fitHeight={800}
                    title={`${selected.title} 미리보기 화면`}
                  />
                </div>
                {selected.next.length > 0 && (
                  <section
                    aria-label="이어지는 화면"
                    className="mt-5 flex flex-wrap items-center gap-2"
                  >
                    <h3 className="mr-2 text-sm font-bold">이어지는 화면</h3>
                    {selected.next.map((item) => (
                      <Button
                        key={`${item.screenId}:${item.label}`}
                        size="sm"
                        variant="secondary"
                        onClick={() => previewNavigate(selected, item.screenId, item.stateId)}
                      >
                        {item.label}
                      </Button>
                    ))}
                  </section>
                )}
              </div>
              <Details screen={selected} stateId={selectedState} preview={preview} />
            </div>
          )}
          {view === 'single' && !selected && (
            <p role="status" className="p-5">
              요청한 화면이 없습니다. 화면 목록에서 선택하세요.
            </p>
          )}
          {view === 'all' && (
            <div className="grid min-w-0 grid-cols-1 gap-5 p-4 sm:p-5 2xl:grid-cols-2">
              {screens.map((screen) => {
                const stateId = cardState(screen);
                return (
                  <section key={screen.id} className="min-w-0 border-2 border-line-strong bg-panel">
                    <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line-strong p-4">
                      <div>
                        <h3 className="font-bold">{screen.title}</h3>
                        <p className="mt-1 text-xs">{screen.status}</p>
                      </div>
                      <StateSelect
                        screen={screen}
                        value={stateId}
                        onChange={(id) => changeState(screen.id, id)}
                      />
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => openScreen(screen.id, stateId)}
                      >
                        개별로 보기
                      </Button>
                    </div>
                    <div className="min-w-0" role="region" aria-label={`${screen.title} 미리보기`}>
                      <PreviewFrame
                        screenId={screen.id}
                        stateId={stateId}
                        onNavigate={(id, next) => previewNavigate(screen, id, next)}
                        device={device}
                        fitHeight={384}
                        title={`${screen.title} 미리보기 화면`}
                        inline
                        lazy
                      />
                    </div>
                  </section>
                );
              })}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
