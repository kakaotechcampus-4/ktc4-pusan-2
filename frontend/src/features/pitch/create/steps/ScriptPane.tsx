import { useEffect, useId, useRef, useState, type RefObject } from 'react';
import { scanSlideMarkers, type MarkerScan } from '@/shared/lib/slideMarkers';
import { useCreateStore } from '../createStore';
import { CaretIcon, CheckIcon, DocIcon, LinkIcon } from '../icons';
import {
  canSaveMapping,
  countChars,
  estimateDurationMs,
  formatEstimate,
  MAX_SCRIPT_CHARS,
  scriptLength,
  type ScriptVersion,
  type SlideVersion,
} from '../lib/draft';
import { computeGate } from '../lib/gate';
import { PdfPage } from '@/shared/ui/PdfPage';
import { retryScriptMapping, startScriptMapping } from '../scriptParse';
import { usePdfDocument } from '@/shared/lib/usePdfDocument';
import { NeedPitch } from './NeedPitch';
import { PaneHeading } from './PaneHeading';

/**
 * 목업 대본 입력 · 대본 매핑 확인은 **같은 대본 버전의 두 상태**입니다.
 * `blocks` 가 없으면 입력, 있으면 매핑 확인.
 *
 * "대본 매핑"은 서버에 새 버전으로 올리고(`POST /pitches/{id}/scripts`) AI 가 나눈 결과를
 * 폴링으로 받습니다 (`scriptParse.ts`). AI 는 "[슬라이드 1]" 같은 **명시적 구분자**로만 나누고,
 * 구분자가 없으면 대본 전체를 한 슬라이드로 둡니다 — 그래서 작성 안내에 구분자를 적어 둡니다.
 *
 * 매핑 확인에서 "저장"하면 그 슬라이드 + 대본이 연습할 조합이 됩니다. 페이지 하단에서 시작 전 점검으로 갑니다.
 * 서버는 대본이 어느 슬라이드에 맞춘 것인지 모르므로, 장수 비교는 화면이 합니다.
 */

const PLACEHOLDER = [
  '[슬라이드 1]',
  '안녕하세요. 오늘은 …',
  '',
  '[슬라이드 2]',
  '처음에 저희가 마주한 문제는 …',
].join('\n');

/**
 * 입력 중 안내. 지금 대본대로 매핑하면 어떻게 나뉠지 미리 알려 줍니다.
 * 막지는 않습니다 — 기존 표기도 안내용으로 인식합니다 (`shared/lib/slideMarkers`).
 */
function markerHint(scan: MarkerScan, pageCount: number, tooLong: boolean): string | null {
  if (tooLong) {
    return `대본은 ${MAX_SCRIPT_CHARS.toLocaleString()}자(공백 포함)까지 매핑할 수 있어요. 조금 줄여 주세요.`;
  }
  if (scan.numbers.length === 0) {
    return '“[슬라이드 1]”처럼 슬라이드마다 구분해 주세요. 구분이 없으면 대본 전체가 한 슬라이드로 연결돼요.';
  }
  if (scan.leadingText) {
    return '첫 구분 앞에는 제목 한 줄만 둘 수 있어요. 그보다 많으면 나뉘지 않으니 “[슬라이드 1]”을 앞으로 옮겨 주세요.';
  }
  const last = Math.max(...scan.numbers);
  if (pageCount > 0 && last !== pageCount) {
    return `구분은 ${last}번까지, 슬라이드는 ${pageCount}장이에요. 장수를 맞춰야 연습을 시작할 수 있어요.`;
  }
  return null;
}

function mapLabel(pending: boolean): string {
  return pending ? '나누는 중…' : '대본 매핑';
}

/** 편집기 왼쪽 줄 번호. 목업처럼 빈 편집기에도 몇 줄은 보입니다 */
const MIN_GUTTER_LINES = 12;

/* ------------------------------------------------------------------ */
/* 대본 입력                                                           */
/* ------------------------------------------------------------------ */

/** 올린 슬라이드가 여럿이면 버전을 보고 고릅니다. */
function SlideLink({ script, linked }: { script: ScriptVersion; linked: SlideVersion | null }) {
  // 배열을 통째로 꺼내 밖에서 거릅니다 — 셀렉터 안에서 filter 하면 매번 새 배열이라 무한히 다시 그립니다
  const allSlides = useCreateStore((s) => s.draft.slides);
  const slides = allSlides.filter((v) => v.pageCount !== null);
  const linkSlides = useCreateStore((s) => s.linkSlides);
  const select = useCreateStore((s) => s.select);
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const menu = container.current;
    const options = menu?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]');
    const selected = menu?.querySelector<HTMLButtonElement>('[aria-checked="true"]');
    (selected ?? options?.[0])?.focus();
    const dismiss = (event: PointerEvent) => {
      if (!menu?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, [open]);

  if (slides.length === 0) {
    return (
      <button
        type="button"
        onClick={() => select('slides')}
        className="flex h-10 items-center gap-2 rounded-lg border border-dashed border-line-strong bg-white px-4 text-sm text-ink/80 hover:border-ink hover:text-ink"
      >
        <LinkIcon />
        연결할 슬라이드가 없어요 · 슬라이드 올리기
      </button>
    );
  }

  return (
    <div
      ref={container}
      className="relative"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          setOpen(false);
          trigger.current?.focus();
        }
      }}
    >
      <button
        ref={trigger}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen(!open)}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            setOpen(true);
          }
        }}
        className="flex h-10 items-center gap-3 rounded-lg border border-line-strong bg-white px-4 text-sm hover:border-ink aria-expanded:border-ink aria-expanded:bg-panel"
      >
        <LinkIcon />
        <span className="font-bold">연결할 슬라이드</span>
        <span className="tabular">
          {linked ? `V${linked.version} · ${linked.pageCount}장` : '고르기'}
        </span>
        <span className={open ? 'rotate-180' : ''}>
          <CaretIcon open />
        </span>
      </button>
      {open && (
        <div
          id={menuId}
          role="menu"
          aria-label="연결할 슬라이드"
          className="absolute left-0 top-full z-30 mt-2 max-h-60 w-full overflow-y-auto rounded-xl border border-line-strong bg-white p-1.5"
          onKeyDown={(event) => {
            const options = Array.from(
              event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]'),
            );
            const index = options.indexOf(document.activeElement as HTMLButtonElement);
            let next = index;
            if (event.key === 'ArrowDown') next = (index + 1) % options.length;
            else if (event.key === 'ArrowUp') next = (index - 1 + options.length) % options.length;
            else if (event.key === 'Home') next = 0;
            else if (event.key === 'End') next = options.length - 1;
            else return;
            event.preventDefault();
            options[next]?.focus();
          }}
        >
          {slides.map((v) => (
            <button
              key={v.version}
              type="button"
              role="menuitemradio"
              aria-checked={v.version === linked?.version}
              tabIndex={-1}
              onClick={() => {
                if (v.version !== linked?.version) linkSlides(script.version, v.version);
                setOpen(false);
                trigger.current?.focus();
              }}
              className="tabular flex min-h-10 w-full items-center gap-3 rounded-lg px-3 text-left text-sm hover:bg-panel focus-visible:bg-panel aria-checked:bg-coral-wash aria-checked:font-bold"
            >
              <span className="flex h-4 w-4 items-center">
                {v.version === linked?.version && <CheckIcon />}
              </span>
              <span>V{v.version}</span>
              <span className="ml-auto">{v.pageCount}장</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Editor({ script, linked }: { script: ScriptVersion; linked: SlideVersion | null }) {
  const editScript = useCreateStore((s) => s.editScript);

  const chars = countChars(script.text);
  const estimate = formatEstimate(estimateDurationMs(script.text));
  const pending = script.parse.status === 'pending';
  const failure = script.parse.status === 'failed' ? script.parse.message : null;
  const lineCount = Math.max(script.text.split('\n').length, MIN_GUTTER_LINES);
  const pageCount = linked?.pageCount ?? 0;
  const scan = scanSlideMarkers(script.text);
  const tooLong = scriptLength(script.text) > MAX_SCRIPT_CHARS;
  const hint = chars > 0 ? markerHint(scan, pageCount, tooLong) : null;
  // 나뉠 장수 = 마지막 구분 번호 (건너뛴 번호는 빈 자리로 남습니다 — beAdapter)
  const markerCount = scan.leadingText || scan.numbers.length === 0 ? 0 : Math.max(...scan.numbers);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PaneHeading title="2. 대본 연결" subtitle="선택한 슬라이드에 맞춰 대본을 준비해 주세요." />

      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <SlideLink script={script} linked={linked} />
          {pageCount > 0 && (
            <span
              aria-live="polite"
              className={[
                'tabular rounded-lg px-3 py-2 text-sm font-bold',
                markerCount === pageCount ? 'bg-panel text-ink' : 'bg-coral-wash text-ink',
              ].join(' ')}
            >
              구분 {markerCount} / {pageCount}
            </span>
          )}
        </div>
        <div className="flex items-center gap-4">
          <span className="tabular text-sm text-ink/80">
            {chars.toLocaleString()}자 · 예상 {estimate}
          </span>
          <button
            type="button"
            disabled={chars === 0 || pending || tooLong}
            onClick={() => startScriptMapping(script.version)}
            className="h-11 rounded-lg bg-coral-deep px-6 text-sm font-bold text-white hover:bg-ink disabled:cursor-not-allowed disabled:bg-line disabled:text-ink/80"
          >
            {mapLabel(pending)}
          </button>
        </div>
      </div>

      {hint && !failure && (
        <p
          role="status"
          className="mb-3 rounded-lg border border-coral/40 bg-coral-wash px-4 py-2 text-sm"
        >
          {hint}
        </p>
      )}

      {failure && (
        <p role="alert" className="mb-3 flex items-center gap-3 text-sm font-bold text-ink">
          {failure}
          <button
            type="button"
            onClick={() => retryScriptMapping(script.version)}
            className="rounded-lg border border-coral px-3 py-1 text-xs text-ink hover:bg-coral-wash"
          >
            다시 시도
          </button>
        </p>
      )}

      {/*
        줄 번호와 글이 같이 스크롤되도록 한 상자에 둡니다. 줄을 접지 않아야 번호가 맞습니다.
        포커스 표시는 상자 테두리가 맡습니다 — 글 칸의 포커스 링은 스크롤 상자에 잘려 선 하나만 남습니다
      */}
      <div className="create-script-editor flex min-h-0 overflow-auto rounded-lg border border-line bg-white focus-within:border-ink">
        <ol
          aria-hidden="true"
          className="tabular sticky left-0 shrink-0 select-none border-r border-line bg-panel/50 px-4 py-5 text-right text-sm leading-8 text-ink/80"
        >
          {Array.from({ length: lineCount }, (_, i) => (
            <li key={i}>{i + 1}</li>
          ))}
        </ol>
        <textarea
          aria-label="대본"
          value={script.text}
          onChange={(e) => editScript(script.version, e.target.value)}
          // 나누는 동안 고치면 결과가 이 글과 어긋납니다 — 끝날 때까지 잠깐 막습니다
          readOnly={pending}
          placeholder={PLACEHOLDER}
          wrap="off"
          rows={lineCount}
          className="min-w-0 flex-1 resize-none overflow-hidden bg-transparent px-5 py-5 text-sm leading-8 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink placeholder:text-ink/80"
          // 전역 :focus-visible 링은 레이어 밖이라 유틸리티로 못 덮습니다 — 포커스는 바깥 상자 테두리가 보여 줍니다
          style={{ outline: 'none' }}
        />
      </div>

      <p className="mt-3 border-t border-line pt-3 text-sm text-ink/80">
        슬라이드마다 [N] · [슬라이드 N] · 슬라이드 N: 중 하나를 새 줄에 적어 주세요. N은 슬라이드
        번호예요. 구분자가 없으면 대본 전체를 한 슬라이드로 연결해요.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 대본 매핑 확인                                                       */
/* ------------------------------------------------------------------ */

function SlideThumb({ slide, pageNumber }: { slide: SlideVersion | null; pageNumber: number }) {
  const opened = usePdfDocument(slide?.fileUrl);
  return (
    <div className="create-mapping-preview">
      {opened && 'doc' in opened && pageNumber <= opened.doc.numPages ? (
        <PdfPage doc={opened.doc} pageNumber={pageNumber} fit="contain" className="h-full w-full" />
      ) : (
        <span className="tabular text-xs text-ink/80">{pageNumber}</span>
      )}
    </div>
  );
}

/** 나눈 결과가 슬라이드와 맞지 않을 때의 안내. 고칠 곳은 대본의 구분자입니다 */
function MappingNotice({
  segmented,
  blockCount,
  pageCount,
}: {
  segmented: boolean | null;
  blockCount: number;
  pageCount: number;
}) {
  const message = (() => {
    if (segmented === false) {
      return '구분자가 없어 대본 전체를 한 슬라이드로 연결했어요. “[슬라이드 1]”처럼 나눠 적으면 장마다 연결돼요.';
    }
    if (pageCount > 0 && blockCount !== pageCount) {
      return `대본은 ${blockCount}장, 슬라이드는 ${pageCount}장으로 나뉘었어요. 대본의 구분을 확인해 주세요.`;
    }
    return null;
  })();

  if (!message) return null;
  return (
    <p
      role="status"
      className="mb-3 rounded-lg border border-coral/40 bg-coral-wash px-4 py-3 text-sm"
    >
      {message}
    </p>
  );
}

/** 저장 전 안내. 장수가 맞지 않으면 저장이 막힌 이유와 고칠 방법을 그 자리에서 말합니다 */
function SaveHint({
  countsMatch,
  blockCount,
  pageCount,
}: {
  countsMatch: boolean;
  blockCount: number;
  pageCount: number;
}) {
  if (countsMatch)
    return <p className="text-sm text-ink/80">매핑 결과를 확인하고 저장해 주세요.</p>;
  if (pageCount === 0) {
    return <p className="text-sm font-bold text-ink">연결할 슬라이드를 먼저 골라 주세요.</p>;
  }
  return (
    <p className="text-sm">
      <span className="block font-bold text-ink">
        대본은 {blockCount}장, 슬라이드는 {pageCount}장이라 저장할 수 없어요.
      </span>
      <span className="block text-xs text-ink/80">
        창을 닫고 대본 원문 수정에서 “[슬라이드 1]”처럼 장마다 구분을 넣고 다시 매핑해 주세요.
      </span>
    </p>
  );
}

function MappingDialog({
  script,
  linked,
  onClose,
  returnFocus,
}: {
  script: ScriptVersion;
  linked: SlideVersion | null;
  onClose: () => void;
  returnFocus: RefObject<HTMLButtonElement | null>;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current!;
    const previous = returnFocus.current;
    const overflow = document.body.style.overflow;
    dialog.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      dialog.close();
      document.body.style.overflow = overflow;
      previous?.focus({ preventScroll: true });
    };
  }, [returnFocus]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      className="create-mapping-dialog"
      onKeyDown={(event) => {
        if (event.key !== 'Tab') return;
        const controls = [
          ...event.currentTarget.querySelectorAll<HTMLElement>('button, textarea'),
        ].filter((control) => !control.hasAttribute('disabled'));
        const first = controls[0];
        const last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }}
    >
      <header>
        <h2 id={titleId}>매핑 확인·수정</h2>
        <button
          type="button"
          autoFocus
          className="create-secondary"
          onClick={() => ref.current?.close()}
        >
          닫기
        </button>
      </header>
      <MappingEditor script={script} linked={linked} />
    </dialog>
  );
}

function MappingReview({
  script,
  linked,
  open,
  setOpen,
}: {
  script: ScriptVersion;
  linked: SlideVersion | null;
  open: boolean;
  setOpen: (open: boolean) => void;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const unmapScript = useCreateStore((s) => s.unmapScript);
  const blocks = script.blocks ?? [];
  const pages = linked?.pageCount ?? 0;
  const missing = Array.from(
    { length: Math.max(pages, blocks.length) },
    (_, i) => blocks[i] ?? '',
  ).filter((text) => !text.trim()).length;
  return (
    <div>
      <PaneHeading
        title="2. 대본 연결"
        subtitle="장별 대본을 확인하고, 수정한 매핑은 다시 저장해 주세요."
      />
      <div className="create-mapping-summary">
        <div>
          <p className="font-bold">
            슬라이드 V{linked?.version ?? '?'} · {pages}장 / 대본 V{script.version} ·{' '}
            {blocks.length}장
          </p>
          <p role="status" className="mt-2 text-sm">
            {script.saved ? '저장 완료' : '저장 필요'} ·{' '}
            {missing > 0 ? `빈 대본 ${missing}장` : '빈 대본 없음'}
          </p>
        </div>
        <button
          ref={trigger}
          type="button"
          className="create-secondary"
          onClick={() => setOpen(true)}
        >
          매핑 확인/수정
        </button>
      </div>
      <MappingNotice segmented={script.segmented} blockCount={blocks.length} pageCount={pages} />
      {script.parse.status === 'failed' && (
        <p role="alert" className="mt-3 text-sm">
          {script.parse.message}
        </p>
      )}
      {!script.remote && (
        <p className="mt-3 text-sm">
          수정한 문장은 유지됩니다. 확인 창에서 다시 매핑하고 저장해 주세요.
        </p>
      )}
      <button
        type="button"
        className="mt-3 min-h-11 text-sm underline underline-offset-4"
        disabled={script.parse.status === 'pending'}
        onClick={() => unmapScript(script.version)}
      >
        대본 원문 수정
      </button>
      {open && (
        <MappingDialog
          script={script}
          linked={linked}
          returnFocus={trigger}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}

function MappingEditor({ script, linked }: { script: ScriptVersion; linked: SlideVersion | null }) {
  const [selected, setSelected] = useState(0);
  const editMappingBlock = useCreateStore((s) => s.editMappingBlock);
  const saveMapping = useCreateStore((s) => s.saveMapping);
  const draft = useCreateStore((s) => s.draft);
  const chosen = useCreateStore((s) => s.chosen);

  const blocks = script.blocks ?? [];
  const pageCount = linked?.pageCount ?? 0;
  // 대본 블록과 슬라이드 중 많은 쪽만큼 줄을 그립니다 — 남는 쪽이 보여야 어디가 어긋났는지 압니다
  const rows = Array.from(
    { length: Math.max(blocks.length, pageCount) },
    (_, i) => blocks[i] ?? '',
  );
  const active = Math.min(selected, Math.max(0, rows.length - 1));
  const filled = rows.filter((b) => b.trim() !== '').length;
  const currentChoice = { ...chosen, slides: script.slideVersion, script: script.version };
  const gate = computeGate(draft, currentChoice);
  const pending = script.parse.status === 'pending';
  // 저장한 이 조합으로 넘어갑니다. 저장 뒤에 다른 버전을 고쳤으면 진행 조건이 다시 막습니다
  // ★ 장수가 맞아야 저장합니다. 맞지 않는 조합을 저장하면 "저장 완료"인데 "다음"이 막혀 헷갈립니다
  const countsMatch = pageCount > 0 && blocks.length === pageCount;
  const canSave = !script.saved && canSaveMapping(script, linked);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-3 flex flex-wrap gap-2">
        <span className="tabular flex items-center gap-2 rounded-lg bg-coral-wash px-3 py-1.5 text-sm font-bold text-ink">
          슬라이드 V{linked?.version ?? '?'} · {pageCount}장
        </span>
        <span className="tabular flex items-center gap-2 rounded-lg bg-panel px-3 py-1.5 text-sm font-bold">
          <DocIcon className="h-4 w-4 stroke-ink" />
          대본 V{script.version}
        </span>
      </div>

      {!script.remote && (
        <p role="status" className="mb-3 text-sm text-ink">
          수정한 문장은 원문에도 반영됩니다. 수정 대본을 다시 매핑한 뒤 저장해 주세요.
        </p>
      )}
      {script.parse.status === 'failed' && (
        <p role="alert" className="mb-3 text-sm text-ink">
          {script.parse.message}
        </p>
      )}
      <MappingNotice
        segmented={script.segmented}
        blockCount={blocks.length}
        pageCount={pageCount}
      />

      <div className="create-mapping-workspace">
        <nav aria-label="매핑 슬라이드 목록">
          {rows.map((block, i) => (
            <button
              type="button"
              key={i}
              aria-current={active === i ? 'true' : undefined}
              onClick={() => setSelected(i)}
            >
              {i + 1}장 · {i >= pageCount && '슬라이드 없음'}
              {i < pageCount && (block.trim() ? '연결됨' : '대본 없음')}
            </button>
          ))}
        </nav>
        <div className="create-mapping-selected">
          {active < pageCount ? (
            <SlideThumb slide={linked} pageNumber={active + 1} />
          ) : (
            <p>연결된 슬라이드가 없어요.</p>
          )}
          <label>
            <span className="font-bold">{active + 1}장 대본</span>
            <textarea
              aria-label={`슬라이드 ${active + 1} 대본`}
              value={rows[active] ?? ''}
              readOnly={pending || active >= blocks.length}
              onChange={(e) => editMappingBlock(script.version, active, e.target.value)}
              placeholder="이 슬라이드에 연결된 대본이 없어요"
            />
          </label>
        </div>
      </div>
      <div className="create-mapping-pagination">
        <button
          type="button"
          className="create-secondary"
          disabled={active === 0}
          onClick={() => setSelected(active - 1)}
        >
          이전 장
        </button>
        <span aria-live="polite">
          {rows.length === 0 ? 0 : active + 1} / {rows.length}장
        </span>
        <button
          type="button"
          className="create-secondary"
          disabled={active >= rows.length - 1}
          onClick={() => setSelected(active + 1)}
        >
          다음 장
        </button>
      </div>

      <p className="tabular mt-3 text-sm text-ink/80">
        {filled === rows.length
          ? `${rows.length}개 슬라이드 연결 완료`
          : `${rows.length}개 중 ${filled}개 연결 · 빈 슬라이드 ${rows.length - filled}개`}
      </p>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-4 border-t border-line pt-4">
        {script.saved ? (
          <p className="flex items-center gap-3">
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-ink text-white">
              <CheckIcon className="h-4 w-4 stroke-current" />
            </span>
            <span>
              <span className="block text-sm font-bold">
                저장 완료 · 슬라이드 V{linked?.version} / 대본 V{script.version}
              </span>
              <span className="block text-xs text-ink/80">
                {gate.ready ? '이 조합으로 연습을 진행해요.' : gate.message}
              </span>
            </span>
          </p>
        ) : (
          <SaveHint countsMatch={countsMatch} blockCount={blocks.length} pageCount={pageCount} />
        )}

        <div className="flex flex-col items-end gap-1.5">
          <div className="flex flex-wrap justify-end gap-3">
            {(!script.remote || script.parse.status !== 'idle') && (
              <button
                type="button"
                disabled={pending}
                onClick={() =>
                  script.parse.status === 'failed'
                    ? retryScriptMapping(script.version)
                    : startScriptMapping(script.version)
                }
                className="rounded-lg border border-coral px-4 text-sm font-bold text-ink disabled:opacity-50"
              >
                {pending ? '매핑 중…' : '수정 대본 매핑'}
              </button>
            )}
            <button
              type="button"
              disabled={!canSave}
              onClick={() => saveMapping(script.version)}
              className="h-12 w-36 rounded-lg border border-ink bg-white text-sm font-bold hover:bg-panel disabled:cursor-not-allowed disabled:border-line disabled:text-ink/80 disabled:hover:bg-white"
            >
              저장
            </button>
          </div>
          <p className="text-xs text-ink/80">매핑을 수정하면 다시 저장한 뒤 진행할 수 있어요.</p>
        </div>
      </div>
    </div>
  );
}

export function ScriptPane({ script }: { script: ScriptVersion | null }) {
  const [reviewOpen, setReviewOpen] = useState(false);
  const previousParse = useRef(script?.parse.status);
  useEffect(() => {
    if (
      previousParse.current === 'pending' &&
      script?.parse.status === 'idle' &&
      script.blocks !== null
    ) {
      setReviewOpen(true);
    }
    previousParse.current = script?.parse.status;
  }, [script?.parse.status, script?.blocks]);
  const addScriptVersion = useCreateStore((s) => s.addScriptVersion);
  const hasPitch = useCreateStore((s) => s.pitchId !== null);
  const linked = useCreateStore(
    (s) => s.draft.slides.find((v) => v.version === script?.slideVersion) ?? null,
  );

  if (!hasPitch) {
    return (
      <div className="flex flex-1 flex-col">
        <PaneHeading title="2. 대본 연결" subtitle="선택한 슬라이드에 맞춰 대본을 준비해 주세요." />
        <NeedPitch what="대본" />
      </div>
    );
  }

  if (script === null) {
    return (
      <div className="flex flex-1 flex-col">
        <PaneHeading title="2. 대본 연결" subtitle="선택한 슬라이드에 맞춰 대본을 준비해 주세요." />
        <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed border-line-strong bg-white">
          <p className="text-sm text-ink/80">아직 대본이 없어요</p>
          <button
            type="button"
            onClick={() => addScriptVersion()}
            className="rounded-lg bg-coral-deep px-5 py-2.5 text-sm font-bold text-white hover:bg-ink"
          >
            대본 작성 시작
          </button>
        </div>
      </div>
    );
  }

  return script.blocks === null ? (
    <Editor script={script} linked={linked} />
  ) : (
    <MappingReview
      key={script.version}
      script={script}
      linked={linked}
      open={reviewOpen}
      setOpen={setReviewOpen}
    />
  );
}
