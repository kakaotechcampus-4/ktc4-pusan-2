import { useCreateStore } from '../createStore';
import { MAX_CRITERIA, type CriteriaVersion } from '../lib/draft';
import { PaneHeading, StatusPill, Surface, outlineBtn, primaryBtn } from './ui';

/**
 * 시안 — 평가기준. 왼쪽에 자유롭게 적고, 오른쪽에서 정리된 결과를 확인한 뒤 저장합니다.
 *
 * ★ 정리는 지금 임시 규칙입니다(`lib/criteria.ts`). 실제로는 AI 가 정리하고,
 *   그때도 이 화면이 요구하는 것은 같습니다 — 정리된 항목, 그리고 **반영하지 못한
 *   문장과 그 이유**. 이유를 보여 주지 않으면 사용자는 적은 기준이 왜 사라졌는지 모릅니다.
 *
 * 채점 방식(자동 채점 · 발화 대조)은 이 시안에서 빠졌습니다. 서버 계약에도 없던 필드입니다.
 */

const PLACEHOLDER =
  '이번 발표에서 확인하고 싶은 기준을 자유롭게 적어 주세요.\n예: 시장 규모와 출처를 말하고, 채움말은 5회 이하로 줄이고 싶어요.';

function Notice({ criteria, stale }: { criteria: CriteriaVersion; stale: boolean }) {
  // 정리하기 전이거나 반영하지 못한 문장이 없으면 일반 안내를 보여 줍니다
  const hasSkipped = criteria.skipped.length > 0 && !stale;

  if (!hasSkipped) {
    return (
      <div className="rounded border border-amber-300 bg-amber-50 p-4 text-sm">
        <p className="flex items-center gap-2 font-bold text-amber-800">
          <span aria-hidden="true">!</span>
          {stale ? '입력문이 바뀌었어요' : '작성 안내'}
        </p>
        <p className="mt-1.5 text-xs leading-relaxed text-ink">
          {stale
            ? '정리된 항목은 이전 입력문 기준이에요. 다시 정리한 뒤 저장해 주세요.'
            : `구체적인 행동이나 목표를 적어 주세요. 기준은 최대 ${MAX_CRITERIA}개까지 반영돼요.`}
          <br />
          {!stale && '정리 과정에서 반영하지 못한 내용이 있으면 이유와 안내를 이곳에 알려드려요.'}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div role="alert" className="rounded border border-amber-300 bg-amber-50 p-4 text-sm">
        <p className="flex items-center gap-2 font-bold text-amber-800">
          <span aria-hidden="true">!</span>
          다시 확인해 주세요
        </p>
        <ul className="mt-2 flex flex-col gap-2.5 text-xs leading-relaxed">
          {criteria.skipped.map((sk) => (
            <li key={sk.text}>
              <q className="font-bold">{sk.text}</q> — {sk.reason}
              <br />
              <span className="text-stone">{sk.example}</span>
            </li>
          ))}
        </ul>
      </div>
      <p className="text-xs text-stone">위 입력문을 수정한 뒤 다시 정리해 주세요.</p>
    </div>
  );
}

function Organized({ criteria, stale }: { criteria: CriteriaVersion; stale: boolean }) {
  if (criteria.items.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 py-10 text-center">
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className="h-16 w-16 text-stone"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.2"
        >
          <path d="M7 3h8l4 4v14H7zM14 3v5h5M10 13h6M10 17h4" />
        </svg>
        <p className="text-sm font-bold">아직 정리된 평가기준이 없어요</p>
        <p className="text-xs text-stone">왼쪽에 기준을 작성하고 정리 버튼을 눌러 주세요.</p>
      </div>
    );
  }

  return (
    <ol className={['flex flex-col gap-3', stale ? 'opacity-50' : ''].join(' ')}>
      {criteria.items.map((item, i) => (
        <li
          key={item.id}
          className="flex items-center gap-4 rounded border border-line-strong bg-panel px-3 py-3.5"
        >
          <span className="tabular flex h-9 w-9 shrink-0 items-center justify-center rounded bg-cream font-mono text-xs font-bold">
            {String(i + 1).padStart(2, '0')}
          </span>
          <span className="text-sm">{item.text}</span>
        </li>
      ))}
    </ol>
  );
}

export function CriteriaPane({ criteria }: { criteria: CriteriaVersion | null }) {
  const addCriteriaVersion = useCreateStore((s) => s.addCriteriaVersion);
  const editCriteriaSource = useCreateStore((s) => s.editCriteriaSource);
  const organize = useCreateStore((s) => s.organize);
  const saveCriteria = useCreateStore((s) => s.saveCriteria);

  if (criteria === null) {
    return (
      <div className="flex flex-1 flex-col gap-4">
        <PaneHeading
          title="평가기준"
          subtitle="원하는 기준을 한 번에 적고, 정리된 결과를 확인해 주세요."
        />
        <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded border-2 border-dashed border-line-strong">
          <p className="text-sm text-stone">아직 평가기준이 없습니다</p>
          <button type="button" onClick={addCriteriaVersion} className={primaryBtn}>
            평가기준 만들기
          </button>
        </div>
      </div>
    );
  }

  const hasSource = criteria.source.trim() !== '';
  const organized = criteria.items.length > 0 || criteria.skipped.length > 0;
  const stale = organized && criteria.source !== criteria.organizedSource;
  const canSave = criteria.items.length > 0 && !stale && !criteria.saved;

  return (
    <div className="flex flex-1 flex-col gap-4">
      <PaneHeading
        title="평가기준"
        subtitle="원하는 기준을 한 번에 적고, 정리된 결과를 확인해 주세요."
      />

      <div className="grid flex-1 gap-4 lg:grid-cols-2">
        {/* ── 왼쪽: 작성 ─────────────────────────────────────── */}
        <Surface className="flex flex-col gap-4 p-5">
          <div>
            <h3 className="text-lg font-bold">평가기준 작성</h3>
            <p className="mt-0.5 text-xs text-stone">문장이나 목록으로 자유롭게 작성해 주세요.</p>
          </div>

          <textarea
            aria-label="평가기준 원문"
            value={criteria.source}
            onChange={(e) => editCriteriaSource(criteria.version, e.target.value)}
            placeholder={PLACEHOLDER}
            rows={7}
            className="min-h-40 resize-y rounded border border-line-strong bg-panel p-4 text-sm leading-relaxed"
          />

          <button
            type="button"
            disabled={!hasSource}
            onClick={() => organize(criteria.version)}
            className={
              organized
                ? `${outlineBtn} !border-coral !text-coral-deep hover:!bg-coral-wash`
                : outlineBtn
            }
          >
            {organized ? '수정해서 다시 정리' : '평가기준 정리'}
          </button>

          <Notice criteria={criteria} stale={stale} />
        </Surface>

        {/* ── 오른쪽: 정리된 결과 ─────────────────────────────── */}
        <Surface className="flex flex-col gap-4 p-5">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <h3 className="text-lg font-bold">정리된 평가기준</h3>
              <StatusPill tone="idle">{criteria.items.length}개 항목</StatusPill>
            </div>
            {criteria.items.length > 0 && (
              <span className="text-xs text-stone">
                {criteria.saved && !stale ? '저장됨' : '아직 저장하지 않았어요'}
              </span>
            )}
          </div>

          <div className="flex flex-1 flex-col">
            <Organized criteria={criteria} stale={stale} />
          </div>

          <div className="flex items-center justify-between gap-4 border-t border-line pt-4">
            <p className="text-xs text-stone">정리된 항목을 확인한 뒤 저장해 주세요.</p>
            <button
              type="button"
              disabled={!canSave}
              onClick={() => saveCriteria(criteria.version)}
              className={`${primaryBtn} min-w-40`}
            >
              {criteria.saved && !stale ? '저장됨' : '평가기준 저장'}
            </button>
          </div>
        </Surface>
      </div>
    </div>
  );
}
