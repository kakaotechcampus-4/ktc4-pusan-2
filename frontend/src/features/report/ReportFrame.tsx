import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';
import { toMessage } from '@/shared/api/errorMessage';
import { ButtonLink } from '@/shared/ui/Button';
import type { TakeReport } from '@/types/report';

/** 리포트 화면의 틀. 밝은 지면(시안 10–12)에 진한 테두리 · 각진 모서리입니다 */
export function ReportFrame({
  title,
  report,
  meta,
  children,
}: {
  title: string;
  report: TakeReport | undefined;
  meta?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="pitch-create min-h-dvh bg-greige text-ink">
      <header className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-x-4 gap-y-3 px-5 pt-6 sm:px-8">
        <div className="flex flex-wrap items-center gap-4">
          <Link to="/" aria-label="홈으로">
            <PitchCoachWordmark />
          </Link>
          <span className="h-5 w-px bg-line-strong" aria-hidden="true" />
          <h1 className="text-lg font-bold">{title}</h1>
        </div>
        {meta && <p className="tabular text-sm text-stone">{meta}</p>}
      </header>

      <main className="mx-auto flex max-w-7xl flex-col gap-6 px-5 pb-12 pt-5 sm:px-8">
        {report && (
          <p className="border-b border-line pb-4 font-bold">
            {report.pitch_title} <span className="mx-2 text-line-strong">/</span> TAKE{' '}
            {report.take_number}
          </p>
        )}
        {children}
      </main>
    </div>
  );
}

/** 불러오는 중 · 불러오지 못함. 둘 다 아니면 null */
export function QueryState({ isPending, error }: { isPending: boolean; error: Error | null }) {
  if (error)
    return (
      <div role="alert" className="border-2 border-ink bg-panel p-8 text-center">
        <p className="font-bold">연습 기록을 불러오지 못했어요.</p>
        <p className="mt-2 text-sm text-stone">{toMessage(error)}</p>
        <div className="mt-5 flex justify-center">
          <ButtonLink to="/" variant="secondary">
            홈으로
          </ButtonLink>
        </div>
      </div>
    );
  if (isPending)
    return (
      <p role="status" className="border-2 border-ink bg-panel p-8 text-center text-stone">
        연습 기록을 불러오는 중이에요.
      </p>
    );
  return null;
}
