import { useState, type ReactNode } from 'react';
import { DraftPage, Mascot } from '../ui';

/**
 * 피치 생성 개편 시안 — 첫 사용자 피드백 (시작 플로우 개선).
 *
 * 실제 화면(`features/pitch/create`)과 무엇이 다른가
 * - 순서: 발표정보부터 → **슬라이드부터**. 올린 파일 이름이 발표 제목이 되고, 시간은 기본값으로 둡니다.
 *   발표정보는 "확인" 단계로 뒤에 옵니다.
 * - 본문 위 단계 표시줄과 단계마다 "다음" 버튼 — 사이드바를 몰라도 끝까지 갑니다.
 * - 사이드바의 버전 목록과 "+ 새 버전"은 그 갈래를 저장한 뒤에 나타납니다. 저장 전에는 단계 이름만.
 * - 달력에서 오늘을 음영으로 강조합니다.
 * - 대본은 자동 매핑 / 직접 매핑 토글. 직접 매핑은 처음부터 슬라이드 옆에 장마다 적습니다.
 *
 * 그림만 있는 시안입니다. 서버를 부르지 않고, 상태는 이 화면 안에만 둡니다.
 */

/** FIRST = 슬라이드를 올리기 전, REVISED = 다 저장한 뒤 대본을 고쳐 V2 가 생긴 때 */
type Variant = 'FIRST' | 'REVISED' | Step;
type Step = 'slides' | 'script' | 'info' | 'criteria';
type ScriptMode = 'auto' | 'manual';
/** 목표(분)와 하한 · 상한 허용오차(초). 기본값은 실제 화면과 같은 5분 · 30초 · 1분 */
interface Time {
  minutes: number;
  lower: number;
  upper: number;
}

const TOLERANCE_STEP_SEC = 30;
const MAX_TOLERANCE_SEC = 5 * 60;

/** "5분" · "30초" · "1분 30초" */
function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m === 0) return `${s}초`;
  return s === 0 ? `${m}분` : `${m}분 ${s}초`;
}

/**
 * REVISED 때 사이드바에 보이는 버전. 버전 규칙은 실제 화면과 같습니다 —
 * 슬라이드 · 대본 · 평가기준이 **각자** 쌓이고, 저장한 버전은 고치지 않고 새 버전을 만듭니다.
 */
const REVISED_VERSIONS: Partial<Record<Step, number[]>> = { script: [1, 2] };

const STEPS: { id: Step; label: string; optional?: boolean }[] = [
  { id: 'slides', label: '슬라이드' },
  { id: 'script', label: '대본' },
  { id: 'info', label: '발표정보 확인' },
  { id: 'criteria', label: '평가기준', optional: true },
];

const VARIANTS: { id: Variant; label: string }[] = [
  { id: 'FIRST', label: '처음 들어옴' },
  { id: 'slides', label: '1 · 슬라이드 올림' },
  { id: 'script', label: '2 · 대본' },
  { id: 'info', label: '3 · 발표정보 확인' },
  { id: 'criteria', label: '4 · 평가기준' },
  { id: 'REVISED', label: '5 · 저장 뒤 대본을 고침 (V2)' },
];

const FILE_NAME = '캡스톤_중간발표_최종.pdf';
const PAGE_COUNT = 5;

/** 파일 이름 → 발표 제목. 확장자를 떼고 _ · - 를 띄어 씁니다. BE 제한 50자 */
const titleFromFile = (name: string) =>
  name
    .replace(/\.pdf$/i, '')
    .replace(/[_-]+/g, ' ')
    .trim()
    .slice(0, 50);

const AUTO_TITLE = titleFromFile(FILE_NAME);

const SAMPLE_SCRIPT = [
  '슬라이드 1',
  '안녕하세요. 오늘은 발표 리허설 코치, 피치코치를 소개하겠습니다.',
  '',
  '슬라이드 2',
  '처음에 저희가 마주한 문제는 혼자 연습할 때 무엇을 고쳐야 할지 모른다는 것이었습니다.',
  '',
  '슬라이드 3',
  '그래서 시선과 말하기를 Take마다 비교해 다음에 고칠 한 가지를 알려 줍니다.',
].join('\n');

/** yyyy-mm-dd (로컬 날짜) */
const toIsoDate = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/** 한 달 달력 칸 — 일요일부터, 앞뒤 달 날짜로 주를 채웁니다. 실제 화면은 `pitch/create/lib/date.ts` */
function monthGrid(year: number, month: number) {
  const start = new Date(year, month, 1 - new Date(year, month, 1).getDay());
  const last = new Date(year, month + 1, 0);
  const end = new Date(year, month, last.getDate() + (6 - last.getDay()));
  const days = [];
  for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
    days.push({
      iso: toIsoDate(d),
      day: d.getDate(),
      weekday: d.getDay(),
      outside: d.getMonth() !== month,
    });
  }
  return days;
}

/** "슬라이드 N" 줄로 나눕니다. 실제로는 서버(AI)가 나눕니다 — 시안에서 결과 모양만 보여 줍니다 */
function splitByMarker(text: string, pages: number): string[] {
  const parts = text
    .split(/^\s*슬라이드\s*\d+\s*$/m)
    .slice(1)
    .map((p) => p.trim());
  return Array.from({ length: pages }, (_, i) => parts[i] ?? '');
}

const MASCOT: Record<Variant, string> = {
  FIRST: '발표 자료부터\n올려 볼까?',
  slides: '제목은 파일 이름으로\n채워 뒀어!',
  script: '슬라이드마다\n대본을 붙여 보자!',
  info: '날짜만 고르면\n거의 끝!',
  criteria: '기준은 골라도 되고\n건너뛰어도 돼!',
  REVISED: '저장한 V1은\n그대로 남아!',
};

const primary =
  'h-11 rounded-lg bg-coral px-6 text-sm font-bold text-white hover:bg-coral-deep disabled:cursor-not-allowed disabled:bg-line disabled:text-stone';
const secondary =
  'h-11 rounded-lg border border-line-strong bg-white px-5 text-sm font-bold hover:bg-panel';

export function PitchCreateDraft() {
  const [variant, setVariant] = useState<Variant>('FIRST');
  const [title, setTitle] = useState(AUTO_TITLE);
  const [date, setDate] = useState('');
  const [time, setTime] = useState<Time>({ minutes: 5, lower: 30, upper: 60 });
  const [mode, setMode] = useState<ScriptMode>('manual');
  const [text, setText] = useState(SAMPLE_SCRIPT);
  const [blocks, setBlocks] = useState(() => splitByMarker(SAMPLE_SCRIPT, PAGE_COUNT));

  const uploaded = variant !== 'FIRST';
  const revised = variant === 'REVISED';
  const step: Step = (() => {
    if (variant === 'FIRST') return 'slides';
    if (variant === 'REVISED') return 'script';
    return variant;
  })();
  const stepIndex = STEPS.findIndex((s) => s.id === step);
  const go = (s: Step) => setVariant(uploaded ? s : 'FIRST');
  const next = () => setVariant(STEPS[stepIndex + 1]?.id ?? 'criteria');
  const prev = () => setVariant(STEPS[stepIndex - 1]?.id ?? 'slides');

  const pane = (() => {
    if (step === 'slides') {
      return uploaded ? (
        <SlidesPane title={title} onTitle={setTitle} onNext={next} />
      ) : (
        <UploadPane onUpload={() => setVariant('slides')} />
      );
    }
    if (step === 'script') {
      return (
        <ScriptPane
          revised={revised}
          mode={mode}
          onMode={setMode}
          text={text}
          onText={setText}
          blocks={blocks}
          onBlock={(i, v) => setBlocks((bs) => bs.map((b, j) => (j === i ? v : b)))}
          onMap={() => {
            setBlocks(splitByMarker(text, PAGE_COUNT));
            setMode('manual');
          }}
          onPrev={prev}
          onNext={next}
        />
      );
    }
    if (step === 'info') {
      return (
        <InfoPane
          title={title}
          onTitle={setTitle}
          date={date}
          onDate={setDate}
          time={time}
          onTime={setTime}
          onPrev={prev}
          onNext={next}
        />
      );
    }
    return <CriteriaPane onPrev={prev} />;
  })();

  return (
    <DraftPage
      title="피치 생성 · 시작 플로우 개편"
      eyebrow="새 피치"
      fitViewport
      variants={VARIANTS}
      variant={variant}
      onVariant={setVariant}
      questions={[
        '피치를 슬라이드를 처음 올릴 때 만들어요 (제목 = 파일 이름, 시간 = 기본값). 올리고 떠나면 그 피치가 홈에 남아요 — 홈에서 어떻게 보일지 정해야 해요.',
        '직접 매핑은 저장할 때 FE가 “슬라이드 N” 구분을 붙여 지금 API(POST /pitches/{id}/scripts)로 올려요. AI가 구분대로만 나누는지 AI 파트와 확인해야 해요.',
        '자동 → 직접으로 바꾸면 적은 대본을 구분대로 나눠 칸에 채워요. 직접 → 자동은 칸을 구분과 함께 이어 붙일지 정해야 해요.',
        '평가기준 방식(옵션 선택형 · agent 추천)은 AI 파트에서 정하는 중이에요.',
        '버전은 지금 규칙 그대로예요 — 슬라이드 · 대본 · 평가기준이 각자 쌓이고, 저장한 버전은 고치지 않고 새 버전을 만들어요. 사이드바에서 갈래를 저장하기 전에는 이름만 보이고, 저장하면 그 아래에 V1과 "+ 새 버전"이 나타나요. 대본의 "+ 새 버전"을 누르면 시안 상태 5가 돼요. "+ 새 버전"은 사이드바에만 둬요.',
      ]}
    >
      <div className="pitch-create-square flex min-h-0 flex-1 overflow-hidden border border-line-strong bg-greige">
        <aside className="flex w-sidebar shrink-0 flex-col gap-4 overflow-y-auto border-r border-line-strong bg-cream/60 p-4">
          <Rail
            step={step}
            stepIndex={stepIndex}
            uploaded={uploaded}
            revised={revised}
            onPick={go}
            // 시안에서는 대본의 새 버전만 그려 둡니다 (상태 5). 슬라이드는 그 화면으로만 갑니다
            onNewVersion={(s) => setVariant(s === 'script' ? 'REVISED' : s)}
          />
          <div className="mt-auto">
            <Mascot who="chichi">{MASCOT[variant]}</Mascot>
          </div>
        </aside>

        <main className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-hidden p-4">
          <SummaryLine uploaded={uploaded} title={title} date={date} time={time} />
          <StepBar stepIndex={stepIndex} uploaded={uploaded} revised={revised} onPick={go} />
          <section className="flex min-h-0 flex-1 flex-col overflow-y-auto rounded-lg border border-line bg-panel p-5">
            {pane}
          </section>
        </main>
      </div>
    </DraftPage>
  );
}

/* ------------------------------------------------------------------ */
/* 틀 — 사이드바 · 정보 줄 · 단계 표시줄                                   */
/* ------------------------------------------------------------------ */

/** 버전이 쌓이는 갈래. 평가기준도 버전이 있지만 방식이 정해질 때까지(AI 파트) 시안에서 뺍니다 */
const VERSIONED: Step[] = ['slides', 'script'];

/**
 * 순서는 단계 표시줄과 같습니다. 저장하기 전에는 단계 이름만 보이고, 그 갈래를 저장하면
 * 아래로 버전 목록과 "+ 새 버전"이 펼쳐집니다 — 저장한 버전은 고치지 않으니 고치는 길이 여기입니다.
 * 저장 안 한 버전(V2)이 있으면 "+ 새 버전"을 막습니다 (실제 화면 `pendingVersion` 과 같습니다).
 */
function Rail({
  step,
  stepIndex,
  uploaded,
  revised,
  onPick,
  onNewVersion,
}: {
  step: Step;
  stepIndex: number;
  uploaded: boolean;
  revised: boolean;
  onPick: (s: Step) => void;
  onNewVersion: (s: Step) => void;
}) {
  return (
    <nav aria-label="발표 자료">
      <p className="mb-2 border-b border-line pb-2 text-sm font-bold">발표 자료</p>
      <ul className="flex flex-col gap-1">
        {STEPS.map((s, i) => {
          const saved = uploaded && VERSIONED.includes(s.id) && (revised || i < stepIndex);
          const versions = saved ? (revised && REVISED_VERSIONS[s.id]) || [1] : undefined;
          const pending = revised && s.id === 'script' ? 2 : null;
          const active = s.id === step && !versions;
          return (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => onPick(s.id)}
                aria-current={active ? 'step' : undefined}
                className={[
                  'flex w-full items-center justify-between gap-2 border-l-2 px-3 py-2 text-left text-sm',
                  active
                    ? 'border-coral bg-coral-wash font-bold'
                    : 'border-transparent hover:bg-panel',
                ].join(' ')}
              >
                <span>{s.label}</span>
              </button>
              {versions && (
                <ul className="ml-4 mt-0.5 flex flex-col gap-0.5 border-l border-line-strong pl-2">
                  {versions.map((v) => {
                    const latest = v === versions.at(-1);
                    const current = s.id === step && latest;
                    // 연습 조합은 저장한 것 — V2 는 아직 저장 전이라 V1 이 연습 중입니다
                    const practicing = v === 1;
                    return (
                      <li key={v}>
                        <span
                          className={[
                            'tabular flex items-center justify-between rounded-r border-l-2 px-2 py-1 text-sm',
                            current ? 'border-coral bg-coral-wash font-bold' : 'border-transparent',
                          ].join(' ')}
                        >
                          V{v}
                          {practicing && <span className="text-xs text-stone">연습 중</span>}
                          {current && !practicing && (
                            <span className="text-xs font-bold text-coral">저장 전</span>
                          )}
                        </span>
                      </li>
                    );
                  })}
                  <li>
                    <button
                      type="button"
                      disabled={pending !== null}
                      onClick={() => onNewVersion(s.id)}
                      className="w-full px-2 py-1 text-left text-sm text-stone hover:text-ink disabled:cursor-not-allowed disabled:text-line-strong"
                    >
                      + 새 버전
                    </button>
                    {pending !== null && (
                      <p className="px-2 pb-1 text-xs text-stone">
                        V{pending}를 저장하면 만들 수 있어요
                      </p>
                    )}
                  </li>
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

function SummaryLine({
  uploaded,
  title,
  date,
  time,
}: {
  uploaded: boolean;
  title: string;
  date: string;
  time: Time;
}) {
  return (
    <dl className="flex flex-wrap gap-x-6 gap-y-1 border-b border-line pb-2 text-sm">
      <div className="flex items-baseline gap-2">
        <dt className="text-xs text-stone">발표 제목</dt>
        <dd className={uploaded ? 'font-bold' : 'text-stone'}>
          {uploaded ? title || '제목 미입력' : '슬라이드를 올리면 채워져요'}
          {uploaded && title === AUTO_TITLE && (
            <span className="ml-2 rounded bg-cream px-1.5 py-0.5 text-xs font-medium text-stone">
              파일 이름
            </span>
          )}
        </dd>
      </div>
      <div className="flex items-baseline gap-2">
        <dt className="text-xs text-stone">발표 날짜</dt>
        <dd className={date ? 'tabular font-medium' : 'text-stone'}>
          {date ? date.replaceAll('-', '.') : '날짜 미정'}
        </dd>
      </div>
      <div className="flex items-baseline gap-2">
        <dt className="text-xs text-stone">발표시간</dt>
        <dd className="tabular font-medium">
          {time.minutes}분 (−{formatDuration(time.lower)} / +{formatDuration(time.upper)})
        </dd>
      </div>
    </dl>
  );
}

/** 지금 어디쯤인지. 슬라이드를 올리기 전에는 뒤 단계로 갈 수 없습니다 — 피치가 아직 없습니다 */
function StepBar({
  stepIndex,
  uploaded,
  revised,
  onPick,
}: {
  stepIndex: number;
  uploaded: boolean;
  revised: boolean;
  onPick: (s: Step) => void;
}) {
  return (
    <ol aria-label="진행 단계" className="grid grid-cols-2 gap-2 lg:grid-cols-4">
      {STEPS.map((s, i) => {
        const current = i === stepIndex;
        const done = uploaded && !current && (revised || i < stepIndex);
        return (
          <li key={s.id}>
            <button
              type="button"
              disabled={!uploaded && i > 0}
              onClick={() => onPick(s.id)}
              aria-current={current ? 'step' : undefined}
              className={[
                'flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left text-sm disabled:cursor-not-allowed disabled:opacity-50',
                current
                  ? 'border-ink bg-panel font-bold'
                  : 'border-line bg-panel/60 hover:border-line-strong',
              ].join(' ')}
            >
              <span
                className={[
                  'tabular flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold',
                  current && 'bg-ink text-white',
                  done && 'bg-coral-wash text-coral-deep',
                  !current && !done && 'border border-line-strong text-stone',
                ]
                  .filter(Boolean)
                  .join(' ')}
              >
                {done ? '✓' : i + 1}
              </span>
              <span>
                {s.label}
                {s.optional && <span className="ml-1 text-xs font-normal text-stone">(선택)</span>}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function PaneHead({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h2 className="text-3xl font-bold">{title}</h2>
        <p className="mt-1.5 text-stone">{subtitle}</p>
      </div>
      {actions}
    </div>
  );
}

function PaneFooter({ hint, children }: { hint?: string; children: ReactNode }) {
  return (
    <div className="mt-auto flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
      <p className="text-sm text-stone">{hint}</p>
      <div className="flex gap-3">{children}</div>
    </div>
  );
}

function PageThumb({ page, className = 'h-20 w-36' }: { page: number; className?: string }) {
  return (
    <div
      className={`tabular flex shrink-0 items-center justify-center rounded-md border border-line bg-cream text-xs text-stone ${className}`}
    >
      {page}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* ① 슬라이드                                                          */
/* ------------------------------------------------------------------ */

function UploadPane({ onUpload }: { onUpload: () => void }) {
  return (
    <div className="flex flex-1 flex-col">
      <PaneHead title="슬라이드" subtitle="발표에 사용할 PDF부터 올려 주세요." />
      <div className="flex flex-1 flex-col items-center justify-center rounded-lg border-2 border-dashed border-line-strong bg-white px-6 py-10 text-center">
        <p className="text-lg font-bold">PDF를 여기에 끌어다 놓으세요</p>
        <p className="mt-1.5 text-sm text-stone">
          파일 이름으로 발표 제목을 채워 드려요 · 나중에 고칠 수 있어요 · 최대 40MB
        </p>
        <button type="button" onClick={onUpload} className={`${primary} mt-6 px-12`}>
          PDF 파일 선택
        </button>
        <p className="mt-2 text-xs text-stone">시안: 누르면 {FILE_NAME} 을 올린 것으로 봐요</p>
      </div>
    </div>
  );
}

function SlidesPane({
  title,
  onTitle,
  onNext,
}: {
  title: string;
  onTitle: (t: string) => void;
  onNext: () => void;
}) {
  return (
    <div className="flex flex-1 flex-col">
      <PaneHead title="슬라이드" subtitle="올린 자료를 확인해 주세요." />

      <div className="mb-4 flex flex-col gap-2 rounded-lg border border-coral/40 bg-coral-wash px-4 py-3">
        <label htmlFor="draft-title" className="text-sm font-bold">
          파일 이름으로 발표 제목을 정했어요
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <input
            id="draft-title"
            value={title}
            maxLength={50}
            onChange={(e) => onTitle(e.target.value)}
            className="h-10 min-w-0 flex-1 rounded-md border border-line-strong bg-white px-3 text-sm outline-none focus:border-ink"
          />
          {title !== AUTO_TITLE && (
            <button
              type="button"
              onClick={() => onTitle(AUTO_TITLE)}
              className="text-xs font-bold text-coral-deep"
            >
              파일 이름으로 되돌리기
            </button>
          )}
        </div>
        <p className="text-xs text-stone">
          여기서 바로 고치거나, 나중에 발표정보에서 고칠 수 있어요.
        </p>
      </div>

      <p className="mb-3 flex items-center gap-2 text-sm">
        <span className="font-bold">{FILE_NAME}</span>
        <span className="tabular text-stone">· {PAGE_COUNT}장</span>
      </p>
      <ul className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: PAGE_COUNT }, (_, i) => (
          <li key={i}>
            <PageThumb page={i + 1} className="aspect-video w-full" />
          </li>
        ))}
      </ul>

      <PaneFooter hint="다음은 슬라이드에 맞춰 대본을 붙여요.">
        <button type="button" onClick={onNext} className={primary}>
          다음 · 대본 →
        </button>
      </PaneFooter>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* ② 대본 — 자동 매핑 / 직접 매핑                                        */
/* ------------------------------------------------------------------ */

const MODES: { id: ScriptMode; label: string; hint: string }[] = [
  { id: 'auto', label: '자동 매핑', hint: '대본을 한 번에 붙여 넣으면 슬라이드별로 나눠요' },
  { id: 'manual', label: '직접 매핑', hint: '슬라이드를 보면서 장마다 대본을 적어요' },
];

function ScriptPane({
  revised,
  mode,
  onMode,
  text,
  onText,
  blocks,
  onBlock,
  onMap,
  onPrev,
  onNext,
}: {
  revised: boolean;
  mode: ScriptMode;
  onMode: (m: ScriptMode) => void;
  text: string;
  onText: (t: string) => void;
  blocks: string[];
  onBlock: (i: number, v: string) => void;
  onMap: () => void;
  onPrev: () => void;
  onNext: () => void;
}) {
  const filled = blocks.filter((b) => b.trim() !== '').length;

  return (
    <div className="flex flex-1 flex-col">
      <PaneHead
        title={revised ? '대본 V2' : '대본'}
        subtitle={MODES.find((m) => m.id === mode)?.hint ?? ''}
        actions={
          <div
            role="radiogroup"
            aria-label="매핑 방식"
            className="flex rounded-lg border border-line-strong bg-cream p-1"
          >
            {MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                role="radio"
                aria-checked={mode === m.id}
                onClick={() => onMode(m.id)}
                className={[
                  'rounded-md px-4 py-2 text-sm font-bold',
                  mode === m.id ? 'bg-ink text-white' : 'text-stone hover:text-ink',
                ].join(' ')}
              >
                {m.label}
              </button>
            ))}
          </div>
        }
      />

      {revised && (
        <p className="mb-4 rounded-lg border border-line-strong bg-cream px-4 py-3 text-sm">
          <span className="block font-bold">V1을 고쳐서 새로 만든 V2예요</span>
          <span className="block text-stone">
            저장한 V1은 그대로 남아 있고, 지금은 슬라이드 V1 + 대본 V1으로 연습해요. V2를 저장하면
            그때부터 V2로 연습해요.
          </span>
        </p>
      )}

      {mode === 'auto' ? (
        <>
          <textarea
            aria-label="대본"
            value={text}
            onChange={(e) => onText(e.target.value)}
            rows={12}
            className="mb-3 w-full flex-1 resize-none rounded-lg border border-line bg-white px-5 py-4 text-sm leading-8 outline-none focus:border-ink"
          />
          <p className="mb-4 text-sm text-stone">
            “슬라이드 1”처럼 장마다 구분해 주세요. 매핑하면 직접 매핑 화면에서 결과를 확인하고 고칠
            수 있어요.
          </p>
          <PaneFooter>
            <button type="button" onClick={onPrev} className={secondary}>
              ← 이전
            </button>
            <button type="button" onClick={onMap} className={primary}>
              대본 매핑
            </button>
          </PaneFooter>
        </>
      ) : (
        <>
          <ol className="mb-3 flex flex-col gap-2.5">
            {blocks.map((block, i) => {
              const empty = block.trim() === '';
              return (
                <li
                  key={i}
                  className="flex items-center gap-5 rounded-lg border border-line bg-white px-4 py-3"
                >
                  <PageThumb page={i + 1} />
                  <span className="tabular w-20 shrink-0 text-sm font-bold">
                    SLIDE {String(i + 1).padStart(2, '0')}
                  </span>
                  <textarea
                    aria-label={`슬라이드 ${i + 1} 대본`}
                    value={block}
                    onChange={(e) => onBlock(i, e.target.value)}
                    placeholder="이 슬라이드에서 할 말을 적어 주세요"
                    rows={3}
                    className={[
                      'min-w-0 flex-1 resize-none rounded-md border px-4 py-3 text-sm leading-relaxed outline-none focus:border-ink',
                      empty ? 'border-dashed border-line-strong' : 'border-line',
                    ].join(' ')}
                  />
                  <span
                    className={[
                      'flex w-16 shrink-0 items-center justify-center gap-1 text-xs font-bold',
                      empty ? 'text-coral' : 'text-ink',
                    ].join(' ')}
                  >
                    {empty ? '비어 있음' : '✓ 입력됨'}
                  </span>
                </li>
              );
            })}
          </ol>
          <PaneFooter
            hint={`${blocks.length}장 중 ${filled}장 입력 · 빈 장이 있어도 저장할 수 있어요`}
          >
            <button type="button" onClick={onPrev} className={secondary}>
              ← 이전
            </button>
            <button type="button" onClick={onNext} className={primary}>
              {revised ? 'V2 저장하고 다음 →' : '저장하고 다음 →'}
            </button>
          </PaneFooter>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* ③ 발표정보 확인                                                       */
/* ------------------------------------------------------------------ */

function InfoPane({
  title,
  onTitle,
  date,
  onDate,
  time,
  onTime,
  onPrev,
  onNext,
}: {
  title: string;
  onTitle: (t: string) => void;
  date: string;
  onDate: (iso: string) => void;
  time: Time;
  onTime: (t: Time) => void;
  onPrev: () => void;
  onNext: () => void;
}) {
  const limitSec = time.minutes * 60;
  // 목표가 줄면 하한도 함께 줄입니다 — 실제 화면(InfoPane `setMinutes`)과 같습니다
  const setMinutes = (m: number) =>
    onTime({ ...time, minutes: m, lower: Math.min(time.lower, m * 60) });
  return (
    <div className="flex flex-1 flex-col">
      <PaneHead title="발표정보 확인" subtitle="채워 둔 값을 확인하고 발표 날짜를 골라 주세요." />

      <div className="mb-5 flex flex-col gap-4 rounded-lg border border-line bg-white px-7 py-5">
        <div className="flex flex-col gap-2">
          <div className="flex items-baseline justify-between">
            <label htmlFor="draft-info-title" className="text-sm font-bold">
              발표 제목
            </label>
            <span className="tabular text-xs text-stone">{title.length} / 50</span>
          </div>
          <input
            id="draft-info-title"
            value={title}
            maxLength={50}
            onChange={(e) => onTitle(e.target.value)}
            className="h-12 rounded-md border border-line-strong bg-white px-4 outline-none focus:border-ink"
          />
          {title === AUTO_TITLE && (
            <p className="text-xs text-stone">{FILE_NAME} 에서 가져왔어요</p>
          )}
        </div>

        <div className="grid grid-cols-1 gap-8 lg:grid-cols-2 lg:gap-0">
          <div className="flex flex-col gap-3 lg:border-r lg:border-line lg:pr-8">
            <div className="flex items-baseline justify-between">
              <p className="text-sm font-bold">발표 날짜</p>
              <span className="tabular text-sm font-bold">
                {date ? date.replaceAll('-', '.') : '—'}
              </span>
            </div>
            <TodayCalendar value={date} onChange={onDate} />
          </div>

          <div className="flex flex-col gap-6 lg:pl-8">
            <div className="flex flex-col gap-2">
              <p className="text-sm font-bold">목표 발표시간</p>
              <Stepper
                label="목표 발표시간"
                value={`${time.minutes}분`}
                size="lg"
                canDecrease={time.minutes > 1}
                canIncrease={time.minutes < 60}
                onDecrease={() => setMinutes(time.minutes - 1)}
                onIncrease={() => setMinutes(time.minutes + 1)}
              />
            </div>

            <div className="flex gap-6">
              <ToleranceField
                label="하한 허용오차"
                hint="목표보다 짧게 발표해도 되는 시간"
                sec={time.lower}
                maxSec={Math.min(MAX_TOLERANCE_SEC, limitSec)}
                onChange={(lower) => onTime({ ...time, lower })}
              />
              <div className="w-px bg-line" aria-hidden="true" />
              <ToleranceField
                label="상한 허용오차"
                hint="목표보다 길게 발표해도 되는 시간"
                sec={time.upper}
                maxSec={MAX_TOLERANCE_SEC}
                onChange={(upper) => onTime({ ...time, upper })}
              />
            </div>

            <p className="flex items-baseline gap-4 border-t border-line pt-4 text-sm">
              <span className="text-stone">허용 범위</span>
              <span className="tabular text-lg font-bold">
                {formatDuration(limitSec - time.lower)} ~ {formatDuration(limitSec + time.upper)}
              </span>
            </p>
          </div>
        </div>
      </div>

      <PaneFooter hint="평가기준은 골라도 되고 건너뛰어도 돼요.">
        <button type="button" onClick={onPrev} className={secondary}>
          ← 이전
        </button>
        <button type="button" onClick={onNext} className={primary}>
          저장하고 다음 →
        </button>
      </PaneFooter>
    </div>
  );
}

const stepButton =
  'flex items-center justify-center rounded-md border border-line-strong bg-white text-xl hover:bg-panel disabled:cursor-not-allowed disabled:text-line-strong disabled:hover:bg-white';

/** −/+ 칸. 실제 화면 `steps/InfoPane.tsx` 의 `Stepper` 와 같은 모양입니다 */
function Stepper({
  label,
  value,
  size,
  canDecrease,
  canIncrease,
  onDecrease,
  onIncrease,
}: {
  label: string;
  value: string;
  size: 'lg' | 'sm';
  canDecrease: boolean;
  canIncrease: boolean;
  onDecrease: () => void;
  onIncrease: () => void;
}) {
  const box = size === 'lg' ? 'h-12 w-16' : 'h-10 w-12';
  return (
    <div className="flex items-stretch gap-2">
      <button
        type="button"
        aria-label={`${label} 줄이기`}
        disabled={!canDecrease}
        onClick={onDecrease}
        className={`${stepButton} ${box}`}
      >
        −
      </button>
      <span
        aria-live="polite"
        className={`tabular flex flex-1 items-center justify-center whitespace-nowrap rounded-md bg-cream font-bold ${size === 'lg' ? 'text-lg' : 'text-sm'}`}
      >
        {value}
      </span>
      <button
        type="button"
        aria-label={`${label} 늘리기`}
        disabled={!canIncrease}
        onClick={onIncrease}
        className={`${stepButton} ${box}`}
      >
        +
      </button>
    </div>
  );
}

function ToleranceField({
  label,
  hint,
  sec,
  maxSec,
  onChange,
}: {
  label: string;
  hint: string;
  sec: number;
  maxSec: number;
  onChange: (sec: number) => void;
}) {
  return (
    <div className="flex flex-1 flex-col gap-2">
      <p className="text-sm font-bold">{label}</p>
      <Stepper
        label={label}
        value={formatDuration(sec)}
        size="sm"
        canDecrease={sec > 0}
        canIncrease={sec + TOLERANCE_STEP_SEC <= maxSec}
        onDecrease={() => onChange(sec - TOLERANCE_STEP_SEC)}
        onIncrease={() => onChange(sec + TOLERANCE_STEP_SEC)}
      />
      <p className="text-xs text-stone">{hint}</p>
    </div>
  );
}

/**
 * 오늘을 음영 + "오늘" 글자로 강조한 달력. 고른 날(코랄 채움)과 겹쳐도 둘 다 보입니다.
 * 실제 화면의 `steps/DatePicker.tsx` 에 옮길 때는 `dayTone` 에 오늘 한 갈래만 더하면 됩니다.
 */
function TodayCalendar({ value, onChange }: { value: string; onChange: (iso: string) => void }) {
  const [now] = useState(() => new Date());
  const today = toIsoDate(now);
  const [view, setView] = useState({ year: now.getFullYear(), month: now.getMonth() });
  const move = (delta: number) =>
    setView(({ year, month }) => {
      const d = new Date(year, month + delta, 1);
      return { year: d.getFullYear(), month: d.getMonth() };
    });
  const onThisMonth = view.year === now.getFullYear() && view.month === now.getMonth();

  return (
    <div className="rounded-lg border border-line bg-white px-4 py-2">
      <div className="mb-1 flex items-center justify-between">
        <button
          type="button"
          onClick={() => move(-1)}
          aria-label="이전 달"
          className="flex h-8 w-8 items-center justify-center rounded hover:bg-panel"
        >
          ‹
        </button>
        <p className="tabular flex items-center gap-2 text-sm font-bold" aria-live="polite">
          {view.year}년 {view.month + 1}월
          {!onThisMonth && (
            <button
              type="button"
              onClick={() => setView({ year: now.getFullYear(), month: now.getMonth() })}
              className="rounded border border-line-strong px-1.5 text-xs font-bold text-stone hover:text-ink"
            >
              오늘
            </button>
          )}
        </p>
        <button
          type="button"
          onClick={() => move(1)}
          aria-label="다음 달"
          className="flex h-8 w-8 items-center justify-center rounded hover:bg-panel"
        >
          ›
        </button>
      </div>

      <div role="grid" aria-label="발표 날짜" className="grid grid-cols-7 gap-y-0.5 text-center">
        {['일', '월', '화', '수', '목', '금', '토'].map((w, i) => (
          <span
            key={w}
            role="columnheader"
            className={`py-1 text-xs font-bold ${i === 0 ? 'text-coral' : 'text-stone'}`}
          >
            {w}
          </span>
        ))}
        {monthGrid(view.year, view.month).map((cell) => {
          const selected = cell.iso === value;
          const isToday = cell.iso === today;
          const tone = (() => {
            if (selected) return 'bg-coral font-bold text-white';
            if (isToday)
              return 'bg-coral-wash font-bold text-coral-deep ring-1 ring-inset ring-coral';
            if (cell.outside) return 'text-line-strong hover:bg-panel';
            if (cell.weekday === 0) return 'text-coral hover:bg-coral-wash';
            return 'text-ink hover:bg-panel';
          })();
          return (
            <button
              key={cell.iso}
              type="button"
              role="gridcell"
              aria-selected={selected}
              aria-label={isToday ? `${cell.iso} 오늘` : cell.iso}
              aria-current={isToday ? 'date' : undefined}
              onClick={() => onChange(cell.iso)}
              className={`tabular mx-auto flex h-10 w-10 flex-col items-center justify-center rounded-md text-sm ${tone}`}
            >
              {cell.day}
              {isToday && <span className="text-xs leading-none">오늘</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* ④ 평가기준 (선택)                                                    */
/* ------------------------------------------------------------------ */

function CriteriaPane({ onPrev }: { onPrev: () => void }) {
  return (
    <div className="flex flex-1 flex-col">
      <PaneHead
        title="평가기준 (선택)"
        subtitle="연습할 때 무엇을 볼지 정해요. 건너뛰어도 연습할 수 있어요."
      />
      <div className="mb-5 flex flex-1 flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed border-line-strong bg-white px-6 py-12 text-center">
        <p className="font-bold">고르는 방식은 AI 파트에서 정하는 중이에요</p>
        <p className="text-sm text-stone">옵션 선택형(칩 · 체크박스) 또는 agent 추천</p>
      </div>
      <PaneFooter>
        <button type="button" onClick={onPrev} className={secondary}>
          ← 이전
        </button>
        <button type="button" className={secondary}>
          건너뛰기
        </button>
        <button type="button" className={primary}>
          연습 시작 →
        </button>
      </PaneFooter>
    </div>
  );
}
