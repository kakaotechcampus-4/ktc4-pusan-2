import { useState } from 'react';
import { Link } from 'react-router';
import { HomeHelpDialog } from './HomeHelpDialog';
import { usePitchList } from '@/shared/api/home';
import { HomePitchCard } from './HomePitchCard';
import { duration, totalElapsed } from './homeFormat';

const primary =
  'inline-flex items-center justify-center gap-5 border-2 border-ink bg-coral px-5 py-3 text-sm font-bold text-white shadow-[4px_4px_0_var(--color-ink)] transition hover:-translate-y-0.5 hover:bg-coral-deep active:translate-y-1 active:shadow-none';
export function HomePage() {
  const [helpOpen, setHelpOpen] = useState(false);
  const home = usePitchList();
  const data = home.data;
  const count = data?.pitches.reduce((sum, pitch) => sum + pitch.takes.length, 0) ?? 0;
  return (
    <div className="pitch-create min-h-dvh bg-greige text-ink">
      <main className="mx-auto max-w-7xl px-6 pb-16 pt-10 lg:px-10 lg:pt-14">
        <div className="flex flex-wrap items-end justify-between gap-5">
          <div>
            <p className="mb-2 font-mono text-[10px] tracking-widest text-stone">MY PITCHES</p>
            <h1 className="text-3xl font-bold">내 피치</h1>
            <p className="mt-3 text-sm text-stone">
              발표 자료와 지금까지의 연습 기록을 확인해 보세요.
            </p>
          </div>
          <Link to="/pitch/new" className={primary}>
            <span aria-hidden="true">＋</span> 새 피치 만들기
          </Link>
        </div>
        {home.isPending && (
          <div
            role="status"
            className="mt-10 border border-line-strong bg-white p-10 text-center text-stone"
          >
            발표 노트를 가져오는 중이에요…
          </div>
        )}
        {/* 불러오는 중에는 isError 도 data 도 없어 아래는 그리지 않습니다 */}
        {home.isError ? (
          <div role="alert" className="mt-10 border border-line-strong bg-white p-10 text-center">
            <h2 className="font-bold">발표 목록을 가져오지 못했어요.</h2>
            <p className="mt-2 text-sm text-stone">잠시 후 다시 시도해 주세요.</p>
            <button
              onClick={() => home.refetch().catch(() => undefined)}
              className={`${primary} mt-5`}
            >
              다시 불러오기
            </button>
          </div>
        ) : (
          data && (
            <>
              <div className="mt-6 grid grid-cols-1 divide-y divide-ink border border-ink bg-white sm:grid-cols-3 sm:divide-x sm:divide-y-0">
                <div className="p-5">
                  <p className="text-xs text-stone">내 발표 노트</p>
                  <p className="mt-2">
                    <strong className="font-mono text-2xl">
                      {data.pitches.length.toString().padStart(2, '0')}
                    </strong>
                    <span className="ml-2 text-sm">개의 피치</span>
                  </p>
                </div>
                <div className="p-5">
                  <p className="text-xs text-stone">지금까지 쌓은 연습</p>
                  <p className="mt-2">
                    <strong className="font-mono text-2xl">
                      {count.toString().padStart(2, '0')}
                    </strong>
                    <span className="ml-2 text-sm">TAKES</span>
                  </p>
                </div>
                <div className="p-5">
                  <p className="text-xs text-stone">누적 발표 시간</p>
                  <p className="mt-3 flex items-center gap-3 text-sm font-bold">
                    {duration(totalElapsed(data.pitches.flatMap((pitch) => pitch.takes)))}
                  </p>
                </div>
              </div>
              <section aria-labelledby="pitches-title" className="mt-12">
                <h2 id="pitches-title" className="mb-5 text-xl font-bold">
                  발표 목록
                </h2>
                {data.pitches.length ? (
                  <>
                    <p className="mb-5 text-xs text-stone">
                      피치별 연습 기록을 펼쳐 발표 시간과 대본 모드를 확인해 보세요.
                    </p>
                    <div className="space-y-5">
                      {data.pitches.map((pitch, index) => (
                        <HomePitchCard key={pitch.pitch_id} pitch={pitch} index={index} />
                      ))}
                    </div>
                  </>
                ) : (
                  <div className="border-2 border-dashed border-line-strong bg-white px-6 py-14 text-center">
                    <p className="font-mono text-xs tracking-widest text-coral-deep">
                      YOUR FIRST PITCH
                    </p>
                    <h3 className="mt-4 text-2xl font-bold">아직은 빈 노트, 곧 멋진 발표.</h3>
                    <p className="mt-3 text-sm leading-7 text-stone">
                      슬라이드 PDF와 대본을 준비해 주세요.
                      <br />
                      치치와 함께 첫 번째 연습을 시작해 봐요.
                    </p>
                    <Link to="/pitch/new" className={`${primary} mt-7`}>
                      ＋ 첫 피치 만들기
                    </Link>
                  </div>
                )}
              </section>
            </>
          )
        )}
        <aside className="mt-10 flex flex-wrap items-center justify-between gap-4 border-y border-line-strong py-5 text-sm">
          <p>
            <span className="mr-3 font-mono text-coral-deep">TIP /</span>연습 전, 슬라이드와 대본을
            한 번 확인해 보세요.
          </p>
          <button
            type="button"
            onClick={() => setHelpOpen(true)}
            className="text-stone hover:text-coral-deep"
          >
            사용방법 살펴보기
          </button>
        </aside>
      </main>
      <footer className="border-t border-line-strong">
        <div className="mx-auto flex max-w-7xl flex-wrap justify-between gap-4 px-6 py-6 text-xs text-stone lg:px-10">
          <span className="font-mono tracking-widest">PITCH COACH · ONE TAKE AT A TIME.</span>
          <div className="flex gap-5">
            <Link to="/terms?from=home" className="hover:text-ink">
              이용약관
            </Link>
            <Link to="/privacy?from=home" className="hover:text-ink">
              개인정보 처리방침
            </Link>
          </div>
        </div>
      </footer>
      {helpOpen && <HomeHelpDialog onDismiss={() => setHelpOpen(false)} />}
    </div>
  );
}
