import { Badge } from '@/shared/ui/Badge';
import { Card } from '@/shared/ui/Panel';
import { missionSourceNote, type PrepareInfo } from './prepareInfo';

/**
 * 이번 Take 미션 (시안 09 · `docs/fe/design/src/drafts/prepare/PrepareMissionDraft.tsx`). 장치 점검 오른쪽 맨 위에 둡니다 —
 * 미션도 "이번 Take 를 어떻게 할지"입니다.
 *
 * 실전 모드에서는 코치가 말하지 않으므로 "끝나고 리포트에서 확인"으로 바꿔 말합니다 (CLAUDE.md 8번).
 * 미션 이유(reason)는 보이지 않습니다 — `prepareInfo.ts` `fromPrepare` 참고.
 */
export function MissionCard({ info, exam }: { info: PrepareInfo; exam: boolean }) {
  const { missions } = info;

  if (!missions?.items.length) {
    return (
      <Card title="이번 Take 미션">
        <p className="text-sm text-ink/80">
          {missions
            ? `Take ${missions.sourceTakeNumber} 리뷰에서 정한 미션이 없어요. 지난번처럼 편하게 연습해 보세요.`
            : '첫 Take라 아직 미션이 없어요. 편하게 연습해 보세요. 끝나면 다음에 해 볼 것을 알려 드려요.'}
        </p>
      </Card>
    );
  }

  const note = missionSourceNote(info);
  return (
    <section className="border-2 border-ink bg-coral-wash p-5 shadow-[4px_4px_0_var(--color-line)] sm:p-6">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-bold">이번 Take 미션</h2>
        <Badge tone="plain">Take {missions.sourceTakeNumber}에서 정함</Badge>
      </div>
      <ol className="mt-3 flex flex-col gap-2">
        {missions.items.map((m, i) => (
          <li key={m.id} className="flex gap-3 border-t border-ink/15 py-3">
            {/* 번호는 보이는 순서입니다 — BE priority 가 1부터 빈틈없이 온다고 믿지 않습니다 */}
            <span className="tabular flex h-6 w-6 shrink-0 items-center justify-center bg-coral-deep text-xs font-bold text-white">
              {i + 1}
            </span>
            <p className="min-w-0 text-sm font-bold">{m.description}</p>
          </li>
        ))}
      </ol>
      {note && <p className="mt-3 text-xs leading-relaxed text-ink/80">{note}</p>}
      <p className="mt-3 text-xs leading-relaxed text-ink/80">
        {exam
          ? '실전 모드에서는 코치가 알려 주지 않아요. 끝난 뒤 리포트에서 결과를 확인해요.'
          : '발표 중에 이 미션을 기준으로 코치가 알려 드려요.'}
      </p>
    </section>
  );
}
