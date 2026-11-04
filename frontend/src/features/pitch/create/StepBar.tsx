import { selectInfoUnsaved, useCreateStore } from './createStore';
import { latestUploadedSlides, STEPS, type PaneNode, type PitchDraft } from './lib/draft';

/**
 * 본문 위 단계 표시줄 — 사이드바를 몰라도 슬라이드부터 평가기준까지 차례로 갑니다 (시안 `pitch-create`).
 *
 * 슬라이드를 올리기 전에는 다른 단계로 가지 못합니다 — 피치가 슬라이드를 올릴 때 생깁니다.
 * 그 뒤로는 막지 않습니다. 아무 단계나 눌러 갈 수 있습니다.
 * 순서(`STEPS`)는 사이드바(`VersionRail`)와 같이 씁니다.
 */

/** 그 단계를 마쳤나 — 저장까지 끝난 것만 셉니다 */
function isDone(node: PaneNode, draft: PitchDraft, infoSaved: boolean): boolean {
  switch (node) {
    case 'slides':
      return latestUploadedSlides(draft) !== null;
    case 'script':
      return draft.scripts.some((v) => v.saved);
    case 'info':
      return infoSaved;
    case 'criteria':
      return draft.criteria.some((v) => v.saved);
  }
}

export function StepBar() {
  const node = useCreateStore((s) => s.node);
  const select = useCreateStore((s) => s.select);
  const draft = useCreateStore((s) => s.draft);
  const infoSaved = useCreateStore((s) => s.pitchId !== null && !selectInfoUnsaved(s));
  const canLeaveSlides = useCreateStore(
    (s) => s.pitchId !== null && (s.autoTitle === null || latestUploadedSlides(s.draft) !== null),
  );

  return (
    <ol aria-label="진행 단계" className="grid shrink-0 grid-cols-2 gap-2 lg:grid-cols-4">
      {STEPS.map((s, i) => {
        const current = s.node === node;
        const done = !current && isDone(s.node, draft, infoSaved);
        return (
          <li key={s.node}>
            <button
              type="button"
              onClick={() => select(s.node)}
              disabled={!canLeaveSlides && s.node !== 'slides'}
              aria-current={current ? 'step' : undefined}
              className={[
                'flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left text-sm disabled:cursor-not-allowed disabled:bg-cream disabled:text-ink/80',
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
                  !current && !done && 'border border-line-strong text-ink/80',
                ]
                  .filter(Boolean)
                  .join(' ')}
              >
                {done ? '✓' : i + 1}
              </span>
              <span>
                {s.label}
                {s.optional && <span className="ml-1 text-xs font-normal text-ink/80">(선택)</span>}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
