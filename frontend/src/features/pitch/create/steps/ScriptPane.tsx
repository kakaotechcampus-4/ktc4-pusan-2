import { useCreateStore } from '../createStore';
import {
  countChars,
  estimateDurationMs,
  formatEstimate,
  type ScriptVersion,
  type SlideVersion,
} from '../lib/draft';
import { computeGate } from '../lib/gate';
import { splitScript } from '../lib/mapping';
import { PaneHeading, outlineBtn, primaryBtn } from './ui';

/**
 * 시안의 대본 입력과 매핑 확인은 **같은 대본 버전의 두 상태**입니다.
 * `blocks` 가 없으면 입력, 있으면 블록 확인.
 *
 * 대본은 슬라이드 한 버전에 붙습니다(`slideVersion`) — 같은 대본도 8장짜리와 12장짜리
 * 슬라이드에서는 다르게 나뉘기 때문입니다. 연결을 바꾸면 지난 매핑은 버립니다.
 *
 * ★ 매핑은 임시 규칙입니다(`lib/mapping.ts`). AI 결과 형식이 정해지면 그 함수만 바뀝니다.
 */

const MIN_LINES = 12;

function slideLabel(slide: SlideVersion): string {
  return slide.pageCount === null
    ? `V${slide.version}`
    : `V${slide.version} · ${slide.pageCount}장`;
}

function Editor({
  script,
  slides,
  linked,
}: {
  script: ScriptVersion;
  slides: SlideVersion[];
  linked: SlideVersion | null;
}) {
  const editScript = useCreateStore((s) => s.editScript);
  const linkSlide = useCreateStore((s) => s.linkSlide);
  const setBlocks = useCreateStore((s) => s.setBlocks);

  const chars = countChars(script.text);
  const estimate = formatEstimate(estimateDurationMs(script.text));
  const pageCount = linked?.pageCount ?? 0;
  const canMap = chars > 0 && pageCount > 0;

  // 줄 번호는 글 줄(\n) 기준입니다. 그래서 textarea 는 줄바꿈하지 않고 가로로 스크롤합니다 —
  // 줄바꿈하면 번호와 줄이 어긋납니다
  const lineCount = Math.max(MIN_LINES, script.text.split('\n').length);

  const ready = slides.filter((v) => v.pageCount !== null);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <PaneHeading title="대본 입력" subtitle="선택한 슬라이드에 맞춰 대본을 준비해 주세요." />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-2 rounded border border-line-strong bg-panel px-3 py-2 text-xs">
          <span className="font-bold">연결할 슬라이드</span>
          {ready.length === 0 ? (
            <span className="text-stone">슬라이드를 먼저 올려 주세요</span>
          ) : (
            <select
              value={linked?.version ?? ''}
              onChange={(e) => linkSlide(script.version, Number(e.target.value))}
              className="tabular bg-transparent font-mono font-bold"
            >
              {linked === null && <option value="">선택</option>}
              {ready.map((v) => (
                <option key={v.version} value={v.version}>
                  {slideLabel(v)}
                </option>
              ))}
            </select>
          )}
        </label>

        <div className="flex items-center gap-4">
          <span className="tabular text-xs text-stone">
            {chars.toLocaleString()}자 · 예상 {estimate}
          </span>
          <button
            type="button"
            disabled={!canMap}
            onClick={() => setBlocks(script.version, splitScript(script.text, pageCount))}
            className={primaryBtn}
          >
            대본 매핑
          </button>
        </div>
      </div>

      <div className="flex min-h-72 flex-1 overflow-auto rounded border border-line bg-panel">
        <div
          aria-hidden="true"
          className="tabular w-12 shrink-0 select-none border-r border-line bg-cream/50 py-5 pr-3 text-right font-mono text-xs leading-7 text-stone"
        >
          {Array.from({ length: lineCount }, (_, i) => (
            <div key={i}>{i + 1}</div>
          ))}
        </div>
        <textarea
          aria-label="대본"
          value={script.text}
          onChange={(e) => editScript(script.version, e.target.value)}
          placeholder="발표할 내용을 그대로 적어 주세요."
          wrap="off"
          rows={lineCount}
          className="min-w-0 flex-1 resize-none overflow-hidden bg-transparent px-5 py-5 text-sm leading-7"
        />
      </div>

      <p className="border-t border-line pt-3 text-xs text-stone">
        {canMap || chars === 0
          ? '입력한 대본을 슬라이드별로 연결합니다.'
          : '연결할 슬라이드를 고르면 대본을 매핑할 수 있어요.'}
      </p>
    </div>
  );
}

function BlockReview({
  script,
  linked,
  onNext,
}: {
  script: ScriptVersion;
  linked: SlideVersion | null;
  onNext: () => void;
}) {
  const draft = useCreateStore((s) => s.draft);
  const chosen = useCreateStore((s) => s.chosen);
  const editBlock = useCreateStore((s) => s.editBlock);
  const saveMapping = useCreateStore((s) => s.saveMapping);
  const clearMapping = useCreateStore((s) => s.clearMapping);

  const blocks = script.blocks ?? [];
  const saved = script.mappingSaved;
  // 다음 버튼은 매핑만이 아니라 발표정보·평가기준까지 본 진행 조건을 따릅니다
  const gate = computeGate(draft, chosen);
  const combo = `슬라이드 V${linked?.version ?? '?'} / 대본 V${script.version}`;
  const connected = blocks.filter((b) => b.trim() !== '').length;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <PaneHeading
          title="대본 매핑 확인"
          subtitle="슬라이드와 대본이 잘 연결되었는지 확인해 주세요."
        />
        <button type="button" onClick={() => clearMapping(script.version)} className={outlineBtn}>
          ← 대본 수정
        </button>
      </div>

      <div className="flex gap-2 text-xs font-bold">
        <span className="rounded border border-coral/40 bg-coral-wash px-3 py-1.5 text-coral-deep">
          슬라이드 V{linked?.version ?? '?'} · {blocks.length}장
        </span>
        <span className="rounded border border-sky-200 bg-sky-50 px-3 py-1.5 text-sky-800">
          대본 V{script.version}
        </span>
      </div>

      <ul className="flex min-h-72 flex-1 flex-col gap-3 overflow-y-auto">
        {blocks.map((block, i) => {
          const empty = block.trim() === '';
          const no = String(i + 1).padStart(2, '0');
          return (
            <li
              key={i}
              className="flex items-center gap-3 rounded border border-line bg-panel p-2.5"
            >
              <span aria-hidden="true" className="select-none px-1 text-stone">
                ⋮⋮
              </span>
              {/* 썸네일은 서버 변환이 붙으면 이미지가 들어옵니다 */}
              <span className="tabular flex aspect-[16/10] w-28 shrink-0 items-center justify-center rounded border border-line bg-white font-mono text-xs text-stone">
                {no}
              </span>
              <span className="tabular w-20 shrink-0 text-center font-mono text-xs font-bold">
                SLIDE {no}
              </span>
              <textarea
                aria-label={`슬라이드 ${no} 대본`}
                value={block}
                onChange={(e) => editBlock(script.version, i, e.target.value)}
                placeholder="이 슬라이드에 해당하는 대본이 없습니다"
                rows={Math.max(2, block.split('\n').length)}
                className="min-w-0 flex-1 resize-none rounded border border-line bg-panel px-3 py-2 text-sm leading-relaxed"
              />
              <span
                className={[
                  'w-20 shrink-0 rounded px-2 py-2 text-center text-xs font-bold',
                  empty ? 'bg-cream text-stone' : 'bg-emerald-50 text-emerald-800',
                ].join(' ')}
              >
                {empty ? '비어 있음' : '✓ 확인됨'}
              </span>
            </li>
          );
        })}
      </ul>

      <p className="text-xs text-stone">
        {connected === blocks.length
          ? `${blocks.length}개 슬라이드 연결 완료`
          : `${blocks.length}개 중 ${connected}개 슬라이드에 대본이 연결됐어요`}
      </p>

      <div className="flex flex-wrap items-center justify-between gap-4 border-t border-line pt-4">
        <p className="flex items-start gap-2 text-sm">
          <span
            aria-hidden="true"
            className={[
              'mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs text-white',
              saved ? 'bg-emerald-700' : 'bg-line-strong',
            ].join(' ')}
          >
            ✓
          </span>
          <span>
            <b>{saved ? `저장 완료 · ${combo}` : `저장 전 · ${combo}`}</b>
            <br />
            <span className="text-xs text-stone">
              {saved ? '이 조합으로 연습을 진행해요.' : '저장하면 이 조합으로 연습할 수 있어요.'}
            </span>
          </span>
        </p>

        <div className="flex flex-col items-end gap-1.5">
          <div className="flex gap-2">
            <button
              type="button"
              disabled={saved}
              onClick={() => saveMapping(script.version)}
              className={`${outlineBtn} min-w-28`}
            >
              저장
            </button>
            <button
              type="button"
              disabled={!gate.ready}
              onClick={onNext}
              className={`${primaryBtn} min-w-36`}
            >
              다음 →
            </button>
          </div>
          <p className="text-xs text-stone">
            {gate.ready ? '매핑을 수정하면 다시 저장한 뒤 진행할 수 있어요.' : gate.message}
          </p>
        </div>
      </div>
    </div>
  );
}

export function ScriptPane({
  script,
  slides,
  onNext,
}: {
  script: ScriptVersion | null;
  slides: SlideVersion[];
  onNext: () => void;
}) {
  const addScriptVersion = useCreateStore((s) => s.addScriptVersion);

  if (script === null) {
    return (
      <div className="flex flex-1 flex-col gap-4">
        <PaneHeading title="대본 입력" subtitle="선택한 슬라이드에 맞춰 대본을 준비해 주세요." />
        <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded border-2 border-dashed border-line-strong">
          <p className="text-sm text-stone">아직 대본이 없습니다</p>
          <button type="button" onClick={() => addScriptVersion()} className={primaryBtn}>
            대본 작성 시작
          </button>
        </div>
      </div>
    );
  }

  const linked = slides.find((v) => v.version === script.slideVersion) ?? null;

  return script.blocks === null ? (
    <Editor script={script} slides={slides} linked={linked} />
  ) : (
    <BlockReview script={script} linked={linked} onNext={onNext} />
  );
}
