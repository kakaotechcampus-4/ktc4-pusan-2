import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { formatDuration } from '@/shared/lib/clock';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';
import type { TakeReport } from '@/types/report';

/**
 * 분석 대기 · 대본 확인이 함께 쓰는 노을 화면 — 게임 로딩 화면처럼 화면 전체가 그림이고, 내용은 대화 상자에 둡니다.
 *
 * 배경 그림(`public/loading/sunset-wide-bird.png`)은 움직이지 않고 화면을 채웁니다 — 비율이 다르면 가장자리만 잘립니다.
 * 대화 상자는 넓은 화면은 오른쪽, 좁은 화면은 아래쪽이라 왼쪽의 치치를 가리지 않습니다.
 */
export function DuskScreen({
  report,
  children,
}: {
  report: TakeReport | undefined;
  children: ReactNode;
}) {
  return (
    <div className="relative flex min-h-dvh flex-col overflow-hidden bg-dusk">
      <img
        src="/loading/sunset-wide-bird.png"
        alt="노을 진 바닷가 도시 위를 가방을 멘 치치가 날아가는 그림"
        width={1672}
        height={941}
        className="absolute inset-0 h-full w-full object-cover object-center"
      />

      <header className="relative z-10 flex flex-wrap items-center justify-between gap-3 px-5 pt-5 sm:px-8">
        <Link to="/" aria-label="홈으로" className="border-2 border-ink bg-panel px-3 py-2">
          <PitchCoachWordmark />
        </Link>
        {report && (
          <p className="tabular bg-dusk px-2 py-1 text-sm font-bold text-dusk-ink">
            {report.pitch_title} / TAKE {report.take_number}
            {report.measures && ` · 발표 길이 ${formatDuration(report.measures.duration_ms)}`}
          </p>
        )}
      </header>

      <main className="relative z-10 flex flex-1 items-end justify-center px-5 pb-8 pt-4 sm:px-8 lg:items-center lg:justify-end lg:pb-4">
        {/* 게임 대화 상자 — 진한 바탕에 두 겹 테두리 */}
        <section className="flex w-full max-w-xl flex-col items-center gap-4 border-4 border-dusk-ink bg-dusk px-5 py-5 text-center text-dusk-ink shadow-[inset_0_0_0_3px_var(--color-dusk),inset_0_0_0_5px_var(--color-dusk-line)] sm:px-8 lg:max-w-md">
          {children}
        </section>
      </main>
    </div>
  );
}

/** "분석 중..." — 점 셋이 차례로 켜집니다. 점은 화면 읽기 프로그램이 읽지 않습니다 */
export function StatusLine({ text }: { text: string }) {
  return (
    <p role="status" className="text-lg font-bold sm:text-xl">
      {text}
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="loading-dot"
          style={{ animationDelay: `${i * 0.25}s` }}
          aria-hidden="true"
        >
          .
        </span>
      ))}
    </p>
  );
}
