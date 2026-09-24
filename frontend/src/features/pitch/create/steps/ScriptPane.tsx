import { useCreateStore } from '../createStore';
import { countChars, estimateDurationMs, formatEstimate, type ScriptVersion } from '../lib/draft';

/**
 * 목업 06(대본 입력)과 07(슬라이드별 매핑 확인)은 **같은 대본 버전의 두 상태**입니다.
 * `blocks` 가 없으면 입력, 있으면 블록 확인.
 *
 * ★ 매핑을 누가 하는지가 아직 안 정해졌습니다. `ai/workspaces/seojin-lee/script-parser`
 *   가 따로 있고 결과 형식이 확정되지 않았습니다. 지금은 문단 단위로 나눠 두고,
 *   실제 매핑이 붙으면 이 함수만 갈아 끼웁니다 — 화면은 그대로입니다.
 */
function splitByParagraph(text: string, slideCount: number): string[] {
  const paragraphs = text
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);
  // 슬라이드보다 문단이 적으면 빈 블록으로 채웁니다 — 장수와 블록 수가 맞아야 합니다
  return Array.from({ length: slideCount }, (_, i) => paragraphs[i] ?? '');
}

function Editor({ script, slideCount }: { script: ScriptVersion; slideCount: number }) {
  const editScript = useCreateStore((s) => s.editScript);
  const setBlocks = useCreateStore((s) => s.setBlocks);

  const chars = countChars(script.text);
  const estimate = formatEstimate(estimateDurationMs(script.text));
  const canMap = chars > 0 && slideCount > 0;

  return (
    <div className="flex flex-1 flex-col">
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h2 className="text-lg font-bold">대본을 입력해주세요</h2>
        <div className="flex items-center gap-3">
          <span className="tabular font-mono text-xs text-stone">
            {chars.toLocaleString()}자 · 예상 {estimate}
          </span>
          <button
            type="button"
            disabled={!canMap}
            onClick={() => setBlocks(script.version, splitByParagraph(script.text, slideCount))}
            className={[
              'rounded px-4 py-2 text-xs font-bold',
              canMap
                ? 'bg-coral text-panel hover:bg-coral-deep'
                : 'cursor-not-allowed bg-cream text-stone',
            ].join(' ')}
          >
            매핑 실행
          </button>
        </div>
      </div>

      <textarea
        aria-label="대본"
        value={script.text}
        onChange={(e) => editScript(script.version, e.target.value)}
        placeholder="발표할 내용을 그대로 적어주세요."
        className="flex-1 resize-none rounded border-2 border-ink bg-panel p-6 text-sm leading-relaxed"
      />

      <p className="mt-2 text-xs text-stone">
        매핑을 실행하면 슬라이드 장수에 맞춰 대본이 나뉩니다
      </p>
    </div>
  );
}

function BlockReview({ script }: { script: ScriptVersion }) {
  const blocks = script.blocks ?? [];

  return (
    <div className="flex flex-1 flex-col">
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h2 className="text-lg font-bold">슬라이드 별 대본을 확인해주세요</h2>
        <div className="flex items-center gap-3">
          <span className="text-xs text-stone">블록을 고치면 새 버전으로 저장됩니다</span>
          <button type="button" className="rounded bg-ink px-4 py-2 text-xs font-bold text-panel">
            V{script.version + 1}로 저장
          </button>
        </div>
      </div>

      <ul className="flex flex-1 flex-col gap-3 overflow-y-auto rounded border-2 border-ink bg-panel p-4">
        {blocks.map((block, i) => {
          const empty = block.trim() === '';
          return (
            <li key={i} className="flex items-stretch gap-0">
              <span
                className={[
                  'flex w-24 shrink-0 items-center justify-center rounded-l text-xs font-bold',
                  empty ? 'bg-cream text-stone' : 'bg-ink text-panel',
                ].join(' ')}
              >
                SLIDE {i + 1}
              </span>
              <p
                className={[
                  'flex-1 rounded-r border px-4 py-3 text-sm leading-relaxed',
                  empty ? 'border-line bg-cream text-stone' : 'border-line bg-coral-wash/40',
                ].join(' ')}
              >
                {empty ? '이 슬라이드에 해당하는 대본이 없습니다' : block}
              </p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function ScriptPane({
  script,
  slideCount,
}: {
  script: ScriptVersion | null;
  slideCount: number;
}) {
  const addScriptVersion = useCreateStore((s) => s.addScriptVersion);

  if (script === null) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded border-2 border-dashed border-line-strong">
        <p className="text-sm text-stone">아직 대본이 없습니다</p>
        <button
          type="button"
          onClick={() => addScriptVersion()}
          className="rounded bg-coral px-5 py-2.5 text-sm font-bold text-panel hover:bg-coral-deep"
        >
          대본 작성 시작
        </button>
      </div>
    );
  }

  return script.blocks === null ? (
    <Editor script={script} slideCount={slideCount} />
  ) : (
    <BlockReview script={script} />
  );
}
