import { useCreateStore } from '../createStore';
import { CheckIcon, DocIcon, LinkIcon } from '../icons';
import {
  countChars,
  estimateDurationMs,
  formatEstimate,
  type ScriptVersion,
  type SlideVersion,
} from '../lib/draft';
import { computeGate } from '../lib/gate';
import { PdfPage } from '../PdfPage';
import { retryScriptMapping, startScriptMapping } from '../scriptParse';
import { usePdfDocument } from '../usePdfDocument';
import { NeedPitch } from './NeedPitch';
import { PaneHeading } from './PaneHeading';

/**
 * 목업 대본 입력 · 대본 매핑 확인은 **같은 대본 버전의 두 상태**입니다.
 * `blocks` 가 없으면 입력, 있으면 매핑 확인.
 *
 * "대본 매핑"은 서버에 새 버전으로 올리고(`POST /pitches/{id}/scripts`) AI 가 나눈 결과를
 * 폴링으로 받습니다 (`scriptParse.ts`). AI 는 "슬라이드 1" 같은 **명시적 구분자**로만 나누고,
 * 구분자가 없으면 대본 전체를 한 슬라이드로 둡니다 — 그래서 작성 안내에 구분자를 적어 둡니다.
 *
 * 매핑 확인에서 "저장"하면 그 슬라이드 + 대본이 연습할 조합이 되고, "다음"으로 장치 점검에 갑니다.
 * 서버는 대본이 어느 슬라이드에 맞춘 것인지 모르므로, 장수 비교는 화면이 합니다.
 */

const PLACEHOLDER = [
  '슬라이드 1',
  '안녕하세요. 오늘은 …',
  '',
  '슬라이드 2',
  '처음에 저희가 마주한 문제는 …',
].join('\n');

function mapLabel(pending: boolean): string {
  return pending ? '나누는 중…' : '대본 매핑';
}

/** 편집기 왼쪽 줄 번호. 목업처럼 빈 편집기에도 몇 줄은 보입니다 */
const MIN_GUTTER_LINES = 12;

/* ------------------------------------------------------------------ */
/* 대본 입력                                                           */
/* ------------------------------------------------------------------ */

/** "연결할 슬라이드 V1 · 12장". 올린 슬라이드가 여럿이면 여기서 고릅니다 */
function SlideLink({ script, linked }: { script: ScriptVersion; linked: SlideVersion | null }) {
  // 배열을 통째로 꺼내 밖에서 거릅니다 — 셀렉터 안에서 filter 하면 매번 새 배열이라 무한히 다시 그립니다
  const allSlides = useCreateStore((s) => s.draft.slides);
  const slides = allSlides.filter((v) => v.pageCount !== null);
  const linkSlides = useCreateStore((s) => s.linkSlides);
  const select = useCreateStore((s) => s.select);

  if (slides.length === 0) {
    return (
      <button
        type="button"
        onClick={() => select('slides')}
        className="flex h-10 items-center gap-2 rounded-lg border border-dashed border-line-strong bg-white px-4 text-sm text-stone hover:border-ink hover:text-ink"
      >
        <LinkIcon />
        연결할 슬라이드가 없어요 · 슬라이드 올리기
      </button>
    );
  }

  return (
    <label className="flex h-10 items-center gap-2 rounded-lg border border-line-strong bg-white px-4 text-sm">
      <LinkIcon />
      <span className="font-bold">연결할 슬라이드</span>
      <select
        aria-label="연결할 슬라이드"
        value={linked?.version ?? ''}
        onChange={(e) => linkSlides(script.version, Number(e.target.value))}
        className="tabular cursor-pointer appearance-none bg-transparent pr-1 outline-none"
      >
        {linked === null && <option value="">고르기</option>}
        {slides.map((v) => (
          <option key={v.version} value={v.version}>
            V{v.version} · {v.pageCount}장
          </option>
        ))}
      </select>
    </label>
  );
}

function Editor({ script, linked }: { script: ScriptVersion; linked: SlideVersion | null }) {
  const editScript = useCreateStore((s) => s.editScript);

  const chars = countChars(script.text);
  const estimate = formatEstimate(estimateDurationMs(script.text));
  const pending = script.parse.status === 'pending';
  const failure = script.parse.status === 'failed' ? script.parse.message : null;
  const lineCount = Math.max(script.text.split('\n').length, MIN_GUTTER_LINES);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PaneHeading title="대본 입력" subtitle="선택한 슬라이드에 맞춰 대본을 준비해 주세요." />

      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <SlideLink script={script} linked={linked} />
        <div className="flex items-center gap-4">
          <span className="tabular text-sm text-stone">
            {chars.toLocaleString()}자 · 예상 {estimate}
          </span>
          <button
            type="button"
            disabled={chars === 0 || pending}
            onClick={() => startScriptMapping(script.version)}
            className="h-11 rounded-lg bg-coral px-6 text-sm font-bold text-white hover:bg-coral-deep disabled:cursor-not-allowed disabled:bg-line disabled:text-stone"
          >
            {mapLabel(pending)}
          </button>
        </div>
      </div>

      {failure && (
        <p role="alert" className="mb-3 flex items-center gap-3 text-sm font-bold text-coral">
          {failure}
          <button
            type="button"
            onClick={() => retryScriptMapping(script.version)}
            className="rounded-lg border border-coral px-3 py-1 text-xs text-coral-deep hover:bg-coral-wash"
          >
            다시 시도
          </button>
        </p>
      )}

      {/*
        줄 번호와 글이 같이 스크롤되도록 한 상자에 둡니다. 줄을 접지 않아야 번호가 맞습니다.
        포커스 표시는 상자 테두리가 맡습니다 — 글 칸의 포커스 링은 스크롤 상자에 잘려 선 하나만 남습니다
      */}
      <div className="flex min-h-0 flex-1 overflow-auto rounded-lg border border-line bg-white focus-within:border-ink">
        <ol
          aria-hidden="true"
          className="tabular sticky left-0 shrink-0 select-none border-r border-line bg-panel/50 px-4 py-5 text-right text-sm leading-8 text-stone"
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
          className="min-w-0 flex-1 resize-none overflow-hidden bg-transparent px-5 py-5 text-sm leading-8 outline-none placeholder:text-stone"
          // 전역 :focus-visible 링은 레이어 밖이라 유틸리티로 못 덮습니다 — 포커스는 바깥 상자 테두리가 보여 줍니다
          style={{ outline: 'none' }}
        />
      </div>

      <p className="mt-3 border-t border-line pt-3 text-sm text-stone">
        입력한 대본을 슬라이드별로 연결합니다. 슬라이드마다 “슬라이드 1” · “Slide 1” · “1.” 처럼
        구분해 적어 주세요 — 구분이 없으면 대본 전체를 한 슬라이드로 연결해요.
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
    <div className="flex h-20 w-36 shrink-0 items-center justify-center overflow-hidden rounded-md border border-line bg-panel">
      {opened && 'doc' in opened ? (
        <PdfPage doc={opened.doc} pageNumber={pageNumber} fit="width" className="w-full" />
      ) : (
        <span className="tabular text-xs text-stone">{pageNumber}</span>
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
      return '구분자가 없어 대본 전체를 한 슬라이드로 연결했어요. “슬라이드 1”처럼 나눠 적으면 장마다 연결돼요.';
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

function MappingReview({
  script,
  linked,
  onStart,
}: {
  script: ScriptVersion;
  linked: SlideVersion | null;
  onStart: () => void;
}) {
  const unmapScript = useCreateStore((s) => s.unmapScript);
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
  const filled = rows.filter((b) => b.trim() !== '').length;
  const gate = computeGate(draft, chosen);
  // 저장한 이 조합으로 넘어갑니다. 저장 뒤에 다른 버전을 고쳤으면 진행 조건이 다시 막습니다
  const canStart = script.saved && gate.ready;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PaneHeading
        title="대본 매핑 확인"
        subtitle="슬라이드와 대본이 잘 연결되었는지 확인해 주세요."
        actions={
          <button
            type="button"
            onClick={() => unmapScript(script.version)}
            className="h-11 rounded-lg border border-line-strong bg-white px-5 text-sm font-bold hover:bg-panel"
          >
            ← 대본 수정
          </button>
        }
      />

      <div className="mb-3 flex flex-wrap gap-2">
        <span className="tabular flex items-center gap-2 rounded-lg bg-coral-wash px-3 py-1.5 text-sm font-bold text-coral-deep">
          슬라이드 V{linked?.version ?? '?'} · {pageCount}장
        </span>
        <span className="tabular flex items-center gap-2 rounded-lg bg-panel px-3 py-1.5 text-sm font-bold">
          <DocIcon className="h-4 w-4 stroke-ink" />
          대본 V{script.version}
        </span>
      </div>

      <MappingNotice
        segmented={script.segmented}
        blockCount={blocks.length}
        pageCount={pageCount}
      />

      <ol className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto">
        {rows.map((block, i) => {
          const empty = block.trim() === '';
          return (
            <li
              key={i}
              className="flex items-center gap-5 rounded-lg border border-line bg-white px-4 py-3"
            >
              <SlideThumb slide={linked} pageNumber={i + 1} />
              <span className="tabular w-20 shrink-0 text-sm font-bold">
                SLIDE {String(i + 1).padStart(2, '0')}
              </span>
              <p
                className={[
                  'min-w-0 flex-1 whitespace-pre-line rounded-md border px-4 py-3 text-sm leading-relaxed',
                  empty ? 'border-dashed border-line-strong text-stone' : 'border-line',
                ].join(' ')}
              >
                {empty ? '이 슬라이드에 연결된 대본이 없어요' : block}
              </p>
              {empty ? (
                <span className="w-20 shrink-0 text-center text-xs font-bold text-coral">
                  비어 있음
                </span>
              ) : (
                <span className="flex w-20 shrink-0 items-center justify-center gap-1 rounded-md border border-line-strong bg-panel py-1.5 text-xs font-bold">
                  <CheckIcon />
                  확인됨
                </span>
              )}
            </li>
          );
        })}
      </ol>

      <p className="tabular mt-3 text-sm text-stone">
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
              <span className="block text-xs text-stone">
                {gate.ready ? '이 조합으로 연습을 진행해요.' : gate.message}
              </span>
            </span>
          </p>
        ) : (
          <p className="text-sm text-stone">매핑 결과를 확인하고 저장해 주세요.</p>
        )}

        <div className="flex flex-col items-end gap-1.5">
          <div className="flex gap-3">
            <button
              type="button"
              disabled={script.saved}
              onClick={() => saveMapping(script.version)}
              className="h-12 w-36 rounded-lg border border-ink bg-white text-sm font-bold hover:bg-panel disabled:cursor-not-allowed disabled:border-line disabled:text-stone disabled:hover:bg-white"
            >
              저장
            </button>
            <button
              type="button"
              disabled={!canStart}
              onClick={onStart}
              className="h-12 w-40 rounded-lg bg-coral text-sm font-bold text-white hover:bg-coral-deep disabled:cursor-not-allowed disabled:bg-line disabled:text-stone"
            >
              다음 →
            </button>
          </div>
          <p className="text-xs text-stone">매핑을 수정하면 다시 저장한 뒤 진행할 수 있어요.</p>
        </div>
      </div>
    </div>
  );
}

export function ScriptPane({
  script,
  onStart,
}: {
  script: ScriptVersion | null;
  /** 매핑 확인의 "다음" — 장치 점검으로 갑니다 */
  onStart: () => void;
}) {
  const addScriptVersion = useCreateStore((s) => s.addScriptVersion);
  const hasPitch = useCreateStore((s) => s.pitchId !== null);
  const linked = useCreateStore(
    (s) => s.draft.slides.find((v) => v.version === script?.slideVersion) ?? null,
  );

  if (!hasPitch) {
    return (
      <div className="flex flex-1 flex-col">
        <PaneHeading title="대본 입력" subtitle="선택한 슬라이드에 맞춰 대본을 준비해 주세요." />
        <NeedPitch what="대본" />
      </div>
    );
  }

  if (script === null) {
    return (
      <div className="flex flex-1 flex-col">
        <PaneHeading title="대본 입력" subtitle="선택한 슬라이드에 맞춰 대본을 준비해 주세요." />
        <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed border-line-strong bg-white">
          <p className="text-sm text-stone">아직 대본이 없어요</p>
          <button
            type="button"
            onClick={() => addScriptVersion()}
            className="rounded-lg bg-coral px-5 py-2.5 text-sm font-bold text-white hover:bg-coral-deep"
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
    <MappingReview script={script} linked={linked} onStart={onStart} />
  );
}
