import { Link } from 'react-router';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';
import { useLogout } from '@/shared/api/useLogout';
import { useHome } from '@/shared/api/home';
import { HomePitchCard } from './HomePitchCard';
import { duration } from './homeFormat';

const primary =
  'inline-flex items-center justify-center gap-5 border-2 border-ink bg-coral px-5 py-3 text-sm font-bold text-white shadow-[4px_4px_0_var(--color-ink)] transition hover:-translate-y-0.5 hover:bg-coral-deep active:translate-y-1 active:shadow-none';
export function HomePage() {
  const home = useHome();
  const logout = useLogout();
  const data = home.data;
  const count = data?.pitches.reduce((sum, pitch) => sum + pitch.takes.length, 0) ?? 0;
  return (
    <div className="min-h-dvh bg-panel text-ink">
      <header className="border-b border-line-strong">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-5 px-6 py-5 lg:px-10">
          <Link to="/" aria-label="PITCH COACH 홈">
            <PitchCoachWordmark />
          </Link>
          <nav aria-label="홈 메뉴" className="flex items-center gap-5 text-xs sm:gap-8 sm:text-sm">
            <Link to="/" aria-current="page" className="border-b-2 border-coral py-2 font-bold">
              내 피치
            </Link>
            <Link to="/welcome#guide" className="hover:text-coral-deep">
              사용방법
            </Link>
            <Link to="/welcome#coaches" className="hidden hover:text-coral-deep sm:block">
              연구실 소개
            </Link>
            <button
              disabled={logout.isPending}
              onClick={() => logout.mutate()}
              className="text-stone hover:text-ink disabled:opacity-50"
            >
              {logout.isPending ? '로그아웃 중…' : '로그아웃'}
            </button>
          </nav>
        </div>
        {logout.isError && (
          <p role="alert" className="px-6 pb-3 text-center text-sm text-coral-deep">
            로그아웃하지 못했어요. 다시 시도해 주세요.
          </p>
        )}
      </header>
      <main className="mx-auto max-w-7xl px-6 pb-16 pt-10 lg:px-10 lg:pt-14">
        <section className="grid overflow-hidden border-2 border-ink bg-cream md:grid-cols-[1fr_280px]">
          <div className="p-7 sm:p-10">
            <p className="mb-4 font-mono text-[11px] tracking-[0.2em] text-coral-deep">
              PITCH LAB / MY DESK
            </p>
            <h1 className="text-3xl font-black leading-tight tracking-tight sm:text-4xl">
              한 번 더 말하면,
              <br />
              조금 더 나다워질 거야.
            </h1>
            <p className="mt-5 text-sm leading-7 text-stone">
              완벽한 발표보다 어제보다 나아진 한 번.
              <br className="sm:hidden" /> 오늘의 연습도 여기서 시작해요.
            </p>
            <div className="mt-7 inline-flex items-center gap-3 border border-ink bg-panel px-4 py-2 text-xs">
              <span className="h-2 w-2 bg-coral" aria-hidden="true" />
              치치의 발표 연구실 <span className="font-mono text-stone">OPEN</span>
            </div>
          </div>
          <div className="relative flex items-end justify-center border-t border-line-strong px-6 pt-10 md:border-l md:border-t-0">
            <span className="absolute top-5 border border-ink bg-panel px-3 py-2 text-xs shadow-[3px_3px_0_var(--color-line-strong)]">
              오늘은 어제보다 덜 떨릴지도! skrr
            </span>
            <img
              src="/onboarding/chichi-portrait-longsleeve.png"
              alt="흰색 롱슬리브를 입은 연구원 치치"
              className="mt-4 h-48 w-48 object-cover object-top mix-blend-multiply [image-rendering:pixelated]"
            />
          </div>
        </section>
        {home.isPending ? (
          <div
            role="status"
            className="mt-10 border border-line-strong p-10 text-center text-stone"
          >
            발표 노트를 가져오는 중이에요…
          </div>
        ) : home.isError ? (
          <div role="alert" className="mt-10 border border-line-strong p-10 text-center">
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
              <div className="mt-6 grid grid-cols-1 divide-y divide-line-strong border border-line-strong sm:grid-cols-3 sm:divide-x sm:divide-y-0">
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
                    {duration(
                      data.pitches.reduce(
                        (total, pitch) =>
                          total +
                          pitch.takes.reduce((sum, take) => sum + (take.take_elapsed ?? 0), 0),
                        0,
                      ),
                    )}
                  </p>
                </div>
              </div>
              <section aria-labelledby="pitches-title" className="mt-12">
                <div className="mb-6 flex flex-wrap items-end justify-between gap-5">
                  <div>
                    <p className="mb-2 font-mono text-[10px] tracking-widest text-stone">
                      MY PITCHES
                    </p>
                    <h2 id="pitches-title" className="text-2xl font-bold">
                      내 피치{' '}
                      <span className="ml-2 text-sm font-normal text-stone">
                        발표 {data.pitches.length}개 · 연습 {count}회
                      </span>
                    </h2>
                  </div>
                  <Link to="/pitch/new" className={primary}>
                    <span aria-hidden="true">＋</span> 새 피치 만들기
                  </Link>
                </div>
                {data.pitches.length ? (
                  <>
                    <p className="mb-5 text-xs text-stone">
                      발표 제목을 누르면 자료와 대본을 확인하고, 연습을 이어갈 수 있어요.
                    </p>
                    <div className="space-y-5">
                      {data.pitches.map((pitch, index) => (
                        <HomePitchCard key={pitch.pitch_id} pitch={pitch} index={index} />
                      ))}
                    </div>
                  </>
                ) : (
                  <div className="border-2 border-dashed border-line-strong bg-cream/40 px-6 py-14 text-center">
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
          <Link to="/welcome#guide" className="text-stone hover:text-coral-deep">
            사용방법 살펴보기 ↗
          </Link>
        </aside>
      </main>
      <footer className="border-t border-line-strong">
        <div className="mx-auto flex max-w-7xl flex-wrap justify-between gap-4 px-6 py-6 text-xs text-stone lg:px-10">
          <span className="font-mono tracking-widest">PITCH COACH · ONE TAKE AT A TIME.</span>
          <div className="flex gap-5">
            <Link to="/terms" className="hover:text-ink">
              이용약관
            </Link>
            <Link to="/privacy" className="hover:text-ink">
              개인정보 처리방침
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
