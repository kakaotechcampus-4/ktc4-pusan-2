import { useCreateStore } from '../createStore';
import { MAX_CRITERIA, SCORING_LABEL, type CriteriaVersion, type ScoringMode } from '../lib/draft';

/**
 * 목업 08 — 평가기준. 최대 5개.
 *
 * ★ 채점 방식(`자동 채점` · `발화 대조`)이 목업에만 있습니다. `types/api.ts` 의
 *   `EvalCriterion` 은 `{ id, order, text }` 뿐이라 필드가 빠져 있고, 서버에는
 *   항목을 담을 테이블 자체가 없습니다 (`Standards` 는 pitch 가 아니라 user 에 달려 있습니다).
 *   여기서 쓰는 모양이 곧 서버에 요구할 모양입니다.
 */

const SCORING_ORDER: ScoringMode[] = ['AUTO', 'TRANSCRIPT'];

export function CriteriaPane({ criteria }: { criteria: CriteriaVersion | null }) {
  const addCriteriaVersion = useCreateStore((s) => s.addCriteriaVersion);
  const addCriterion = useCreateStore((s) => s.addCriterion);
  const editCriterion = useCreateStore((s) => s.editCriterion);
  const removeCriterion = useCreateStore((s) => s.removeCriterion);

  if (criteria === null) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded border-2 border-dashed border-line-strong">
        <p className="text-sm text-stone">아직 평가기준이 없습니다</p>
        <button
          type="button"
          onClick={addCriteriaVersion}
          className="rounded bg-coral px-5 py-2.5 text-sm font-bold text-panel hover:bg-coral-deep"
        >
          평가기준 만들기
        </button>
      </div>
    );
  }

  const rows = Array.from({ length: MAX_CRITERIA }, (_, i) => criteria.items[i] ?? null);

  return (
    <div className="flex flex-1 flex-col">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-lg font-bold">평가기준을 입력해주세요</h2>
        <span className="tabular rounded border border-line-strong bg-cream px-3 py-2 font-mono text-xs font-bold">
          {criteria.items.length} / {MAX_CRITERIA} 입력됨
        </span>
      </div>

      <div className="flex flex-1 flex-col rounded border-2 border-ink bg-panel p-4">
        <ul className="flex flex-col gap-3">
          {rows.map((item, i) => {
            const no = String(i + 1).padStart(2, '0');

            if (item === null) {
              return (
                <li key={`empty-${i}`}>
                  <button
                    type="button"
                    onClick={() => addCriterion(criteria.version)}
                    className="flex w-full items-stretch gap-0 text-left"
                  >
                    <span className="tabular flex w-14 shrink-0 items-center justify-center rounded-l border border-dashed border-line-strong font-mono text-xs text-stone">
                      {no}
                    </span>
                    <span className="flex-1 rounded-r border border-dashed border-line-strong px-4 py-3 text-sm text-stone">
                      + 평가기준 추가
                    </span>
                  </button>
                </li>
              );
            }

            return (
              <li key={item.id} className="flex items-stretch gap-0">
                <span className="tabular flex w-14 shrink-0 items-center justify-center rounded-l bg-ink font-mono text-xs font-bold text-panel">
                  {no}
                </span>
                <div className="flex flex-1 items-center gap-3 rounded-r border border-line bg-panel px-4 py-2">
                  <input
                    aria-label={`평가기준 ${no}`}
                    value={item.text}
                    onChange={(e) =>
                      editCriterion(criteria.version, item.id, { text: e.target.value })
                    }
                    placeholder="발표에서 반드시 지킬 것을 한 줄로"
                    className="flex-1 bg-transparent text-sm outline-none"
                  />

                  {/* 채점 방식 — 두 개뿐이라 토글로 둡니다 */}
                  <button
                    type="button"
                    onClick={() =>
                      editCriterion(criteria.version, item.id, {
                        scoring:
                          SCORING_ORDER[(SCORING_ORDER.indexOf(item.scoring) + 1) % 2] ?? 'AUTO',
                      })
                    }
                    className="shrink-0 rounded border border-line px-2 py-1 text-xs text-stone hover:text-ink"
                  >
                    {SCORING_LABEL[item.scoring]}
                  </button>

                  <button
                    type="button"
                    onClick={() => removeCriterion(criteria.version, item.id)}
                    aria-label={`평가기준 ${no} 삭제`}
                    className="shrink-0 px-1 text-stone hover:text-coral"
                  >
                    ✕
                  </button>
                </div>
              </li>
            );
          })}
        </ul>

        <p className="mt-auto pt-6 text-xs text-stone">
          기준은 최대 {MAX_CRITERIA}개까지. 적을수록 피드백이 선명해져요.
        </p>
      </div>
    </div>
  );
}
