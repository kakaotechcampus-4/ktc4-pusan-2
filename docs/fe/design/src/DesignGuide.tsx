/**
 * 화면 설계서 뷰어. 목록(기초 · 화면)은 catalog, 미리보기는 PreviewFrame(iframe 안의 ScreenPreview)이 맡는다.
 * 고른 화면 · 상태 · 보기 방식 · 미리보기 크기는 주소(`#/?screen=&state=&view=&device=&w=&h=&rotate=`)에 두어
 * 새로고침 · 뒤로 가기에서 이어진다. 옛 시안 주소(`#/report` 등)는 같은 이름의 화면으로 바꿔 연다.
 */
import { useEffect, useRef, useState, type RefObject } from 'react';
import { Navigate, Route, Routes, useParams, useSearchParams } from 'react-router';
import { Button } from '@/ui/Button';
import { PRODUCT_ORIGIN, entries, screens } from './catalog';
import { FramePage } from './FramePage';
import {
  CUSTOM_LIMIT,
  DEVICES,
  FRAME_UPDATED_EVENT,
  PHONE_MAX_WIDTH,
  frameUrl,
  isDevice,
  previewSize,
  type Device,
} from './frame';
import { PreviewFrame } from './PreviewFrame';
import type { GuideEntry } from './model';

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

/**
 * 옛 시안 주소 `/:name`(예: `#/report`)을 같은 ID의 항목으로 바꾼다. 시안은 지웠지만(README "시안 관리")
 * 공유된 주소가 남아 있을 수 있어서다. 없는 이름이면 기본 화면
 */
function LegacyDraftRoute() {
  const { name = '' } = useParams();
  const to = entries.some((entry) => entry.id === name)
    ? `/?${new URLSearchParams({ screen: name })}`
    : '/';
  return <Navigate to={to} replace />;
}

/** 그 항목에 있는 상태 ID면 그대로, 아니면 undefined */
function validState(screen: GuideEntry, id: string | null | undefined) {
  return screen.states.find((state) => state.id === id)?.id;
}

/** 목록 갈래의 이름. 전체 보기는 고른 항목과 같은 갈래만 나란히 둔다 */
const KIND_LABEL: Record<GuideEntry['kind'], string> = {
  foundation: '기초',
  component: '컴포넌트',
  screen: '화면',
};

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
  const filtered = entries.filter((screen) =>
    `${screen.title} ${screen.id} ${screen.group} ${screen.summary} ${screen.kind === 'screen' ? screen.route : ''}`
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
          placeholder="이름 · 경로 검색"
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

/** 컴포넌트의 속성 표 */
function Props({ rows }: { rows: [string, string, string][] }) {
  if (rows.length === 0) return null;
  return (
    <section className="border-t border-line pt-4">
      <h3 className="mb-2 text-sm font-bold">속성</h3>
      <dl className="flex flex-col gap-2 text-sm">
        {rows.map(([name, values, description]) => (
          <div key={name}>
            <dt className="font-mono text-xs font-bold">{name}</dt>
            <dd className="break-words font-mono text-xs text-ink/80">{values}</dd>
            <dd className="text-xs">{description}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/** 복사해 쓰는 사용 예. 클립보드를 못 쓰면(http 주소 등) 글을 골라 두고 알린다 */
function CodeSample({ code }: { code: string }) {
  const block = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState<'OK' | 'FAILED' | null>(null);
  function copy() {
    Promise.resolve()
      .then(() => navigator.clipboard.writeText(code))
      .then(() => setCopied('OK'))
      .catch(() => {
        const range = document.createRange();
        if (block.current) range.selectNodeContents(block.current);
        window.getSelection()?.removeAllRanges();
        window.getSelection()?.addRange(range);
        setCopied('FAILED');
      });
  }
  return (
    <section className="border-t border-line pt-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-sm font-bold">사용 예</h3>
        <Button size="sm" variant="secondary" onClick={copy}>
          복사
        </Button>
      </div>
      <pre
        ref={block}
        className="overflow-x-auto bg-ink p-3 font-mono text-xs leading-relaxed text-panel"
      >
        <code>{code}</code>
      </pre>
      <p role="status" className="mt-1 text-xs text-ink/80">
        {copied === 'OK' && '복사했어요.'}
        {copied === 'FAILED' && '복사하지 못해 글을 골라 두었어요. Ctrl+C로 복사해 주세요.'}
      </p>
    </section>
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
  screen: GuideEntry;
  stateId: string;
  preview: RefObject<HTMLIFrameElement>;
}) {
  const state = screen.states.find((item) => item.id === stateId);
  const links = screen.productLinks ?? [];
  const label = `${KIND_LABEL[screen.kind]} 설명`;
  return (
    <aside aria-label={label} className="flex min-w-0 flex-col gap-4 bg-panel p-5">
      <div>
        <p className="text-xs font-bold text-ink/80">{label}</p>
        <h2 className="mt-1 text-lg font-bold">{screen.title}</h2>
        <p className="mt-2 text-sm leading-relaxed">{screen.summary}</p>
        <p className="mt-2 text-sm font-bold">{screen.status}</p>
      </div>
      {state && (
        <section className="border-t border-line pt-4">
          <h3 className="text-sm font-bold">
            {screen.kind === 'screen' ? '현재 상태' : '보기'}: {state.label}
          </h3>
          <p className="mt-2 text-sm leading-relaxed">{state.description}</p>
        </section>
      )}
      <List title={screen.kind === 'screen' ? '동작 규칙' : '쓰는 규칙'} items={screen.rules} />
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
      {screen.kind === 'screen' && <List title="사용하는 컴포넌트" items={screen.components} />}
      {screen.kind === 'component' && (
        <>
          <Props rows={screen.props} />
          <CodeSample code={screen.code} />
        </>
      )}
      {screen.tokens.length > 0 && <TokenList items={screen.tokens} preview={preview} />}
      <List
        title="관련 코드"
        items={
          screen.kind === 'component'
            ? [`설계서 ${screen.file}`, ...screen.sources]
            : screen.sources
        }
      />
      {screen.kind === 'screen' && (
        <section className="border-t border-line pt-4">
          <h3 className="text-sm font-bold">
            {screen.status === '시안' ? '제품에 넣을 경로' : '제품 경로'}
          </h3>
          <p className="mt-2 break-all font-mono text-xs">{screen.route}</p>
          <h3 className="mt-4 text-sm font-bold">지원 기기</h3>
          <p className="mt-2 text-sm">
            {screen.devices === 'pc'
              ? 'PC 전용. 제품은 연습을 PC에서 하도록 안내한다(미지원 브라우저 · 터치 기기).'
              : 'PC · 휴대폰. 휴대폰 크기로 바꿔 배치를 확인한다.'}
          </p>
        </section>
      )}
    </aside>
  );
}

function StateSelect({
  screen,
  value,
  onChange,
}: {
  screen: GuideEntry;
  value: string;
  onChange: (stateId: string) => void;
}) {
  if (screen.states.length === 0) return null;
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs font-bold sm:min-w-44">
      {screen.kind === 'screen' ? '화면 상태' : '보기'}
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

type SizePatch = Partial<Record<'device' | 'w' | 'h' | 'rotate', string | null>>;

/**
 * 미리보기를 어떤 화면 크기로 그릴지: 기기 크기 · 직접 입력 · 가로/세로 돌리기. 칸보다 넓으면 줄여 보인다.
 * 크기만 맞춘다 — 터치 입력 · 모바일 브라우저 동작은 재현하지 않는다(frame.ts DEVICES)
 */
function SizeControls({
  device,
  custom,
  rotated,
  onChange,
}: {
  device: Device;
  /** 직접 입력의 주소 값(돌리기 전) */
  custom: { w: string; h: string };
  rotated: boolean;
  onChange: (patch: SizePatch) => void;
}) {
  const number = `${control} w-24 tabular`;
  return (
    <div className="flex flex-wrap items-end gap-2">
      <label className="flex min-w-0 flex-col gap-1 text-xs font-bold sm:min-w-44">
        미리보기 크기
        <select
          className={control}
          value={device}
          onChange={(event) => {
            const value = event.target.value;
            if (!isDevice(value)) return;
            if (value === 'custom')
              onChange({ device: value, w: custom.w || '1280', h: custom.h || '800' });
            else onChange({ device: value === 'fit' ? null : value, w: null, h: null });
          }}
        >
          {Object.entries(DEVICES).map(([id, item]) => (
            <option key={id} value={id}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      {device === 'custom' && (
        <>
          <label className="flex flex-col gap-1 text-xs font-bold">
            폭
            <input
              type="number"
              inputMode="numeric"
              min={CUSTOM_LIMIT.min}
              max={CUSTOM_LIMIT.max}
              className={number}
              value={custom.w}
              onChange={(event) => onChange({ w: event.target.value })}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-bold">
            높이
            <input
              type="number"
              inputMode="numeric"
              min={CUSTOM_LIMIT.min}
              max={CUSTOM_LIMIT.max}
              className={number}
              value={custom.h}
              onChange={(event) => onChange({ h: event.target.value })}
            />
          </label>
        </>
      )}
      {device !== 'fit' && (
        <Button
          size="sm"
          variant="secondary"
          aria-pressed={rotated}
          className="min-h-11"
          onClick={() => onChange({ rotate: rotated ? null : '1' })}
        >
          가로 · 세로 돌리기
        </Button>
      )}
    </div>
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

  // 주소에 없으면 첫 화면(홈)을 연다. 목록 맨 위는 기초지만 가장 자주 보는 것은 화면이다
  const screenId = params.get('screen') ?? screens[0]?.id;
  const selected = entries.find((entry) => entry.id === screenId);
  const view: View = params.get('view') === 'all' ? 'all' : 'single';
  // 전체 보기는 고른 항목과 같은 갈래(기초 · 화면)만 나란히 둔다
  const allKind = selected?.kind ?? 'screen';
  const deviceParam = params.get('device');
  const device: Device = isDevice(deviceParam) ? deviceParam : 'fit';
  const size = previewSize(params);
  const rotated = params.get('rotate') === '1';
  // PC 전용 화면을 휴대폰 크기로 보면 제품 정책을 함께 알린다
  const phoneOnPcScreen =
    selected?.kind === 'screen' &&
    selected.devices === 'pc' &&
    size !== null &&
    size.width <= PHONE_MAX_WIDTH;
  // 고른 화면은 state, 나머지 카드는 state.<화면 ID>에 상태를 둔다
  const selectedState = selected
    ? (validState(selected, params.get('state')) ??
      validState(selected, params.get(`state.${selected.id}`)) ??
      selected.states[0]?.id ??
      '')
    : '';
  const cardState = (screen: GuideEntry) =>
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
    const target = entries.find((entry) => entry.id === id);
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
  function previewNavigate(source: GuideEntry, id: string, stateId?: string) {
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
  function changeSize(patch: SizePatch) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    // 칸에 맞춤은 돌릴 것이 없다
    if (!next.get('device')) next.delete('rotate');
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
                {view === 'all'
                  ? `전체 ${KIND_LABEL[allKind]}`
                  : (selected?.title ?? '화면을 찾을 수 없음')}
              </h2>
              <p className="mt-1 text-sm text-ink/80">
                {view === 'all'
                  ? `각 ${KIND_LABEL[allKind]}의 상태를 바꿔 비교하세요. 선택은 주소에 저장됩니다.`
                  : '미리보기와 오른쪽 설명을 함께 확인하세요.'}
              </p>
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <SizeControls
                device={device}
                custom={{ w: params.get('w') ?? '', h: params.get('h') ?? '' }}
                rotated={rotated}
                onChange={changeSize}
              />
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
                    {selected.kind === 'screen' && (
                      <span className="font-normal text-ink/80">
                        {selected.devices === 'pc' ? 'PC 전용' : 'PC · 휴대폰'}
                      </span>
                    )}
                    {size && (
                      <span className="tabular font-mono text-xs font-normal">
                        {size.width} × {size.height}
                      </span>
                    )}
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
                {phoneOnPcScreen && (
                  <div
                    role="note"
                    className="mb-4 flex flex-wrap items-center justify-between gap-3 border-2 border-coral-deep bg-coral-wash p-3 text-sm"
                  >
                    <p>
                      이 화면은 <strong>PC 전용</strong>이다. 제품은 발표 연습을 PC에서 하도록
                      안내한다(자동으로 막지는 않는다). 휴대폰 크기 미리보기는 참고용이다.
                    </p>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => openScreen('unsupported', 'mobile')}
                    >
                      휴대폰 안내 화면 보기
                    </Button>
                  </div>
                )}
                <div
                  role="region"
                  aria-label={`${selected.title} 미리보기`}
                  className="min-w-0 border-2 border-line-strong bg-greige"
                >
                  <PreviewFrame
                    frameRef={preview}
                    screenId={selected.id}
                    stateId={selectedState}
                    onNavigate={(id, stateId) => previewNavigate(selected, id, stateId)}
                    size={size}
                    fitHeight={800}
                    title={`${selected.title} 미리보기 화면`}
                  />
                </div>
                {selected.kind === 'screen' && selected.next.length > 0 && (
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
              {entries
                .filter((entry) => entry.kind === allKind)
                .map((screen) => {
                  const stateId = cardState(screen);
                  return (
                    <section
                      key={screen.id}
                      className="min-w-0 border-2 border-line-strong bg-panel"
                    >
                      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line-strong p-4">
                        <div>
                          <h3 className="font-bold">{screen.title}</h3>
                          <p className="mt-1 text-xs">
                            {screen.status}
                            {screen.kind === 'screen' && screen.devices === 'pc'
                              ? ' · PC 전용'
                              : ''}
                          </p>
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
                      <div
                        className="min-w-0"
                        role="region"
                        aria-label={`${screen.title} 미리보기`}
                      >
                        <PreviewFrame
                          screenId={screen.id}
                          stateId={stateId}
                          onNavigate={(id, next) => previewNavigate(screen, id, next)}
                          size={size}
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
