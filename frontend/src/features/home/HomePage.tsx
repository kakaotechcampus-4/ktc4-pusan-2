import { useEffect } from 'react';
import { Link } from 'react-router';

import { useLogout, useSession } from '@/features/auth/useSession';
import { useHome } from '@/shared/api/home';
import type { HomeResponse } from '@/types/api';

function PitchCoachMark() {
  return (
    <svg aria-hidden="true" className="h-8 w-10" viewBox="0 0 34 26" shapeRendering="crispEdges">
      <rect className="fill-coral" x="2" y="0" width="6" height="4" />
      <rect className="fill-coral" x="18" y="0" width="6" height="4" />
      <rect className="fill-coral" x="0" y="4" width="26" height="16" />
      <rect className="fill-coral" x="26" y="8" width="8" height="4" />
      <rect className="fill-coral" x="3" y="20" width="5" height="4" />
      <rect className="fill-coral" x="15" y="20" width="5" height="4" />
      <rect className="fill-ink" x="5" y="9" width="5" height="5" />
      <rect className="fill-ink" x="15" y="9" width="5" height="5" />
    </svg>
  );
}

function Sidebar() {
  const session = useSession();
  const logout = useLogout();

  return (
    <aside className="flex w-full shrink-0 flex-col border-b border-line bg-panel px-6 py-6 lg:min-h-dvh lg:w-sidebar lg:border-b-0 lg:border-r lg:px-8 lg:py-11">
      <Link to="/" className="flex items-center gap-2.5" aria-label="피치코치 홈">
        <PitchCoachMark />
        <span className="text-xl font-extrabold tracking-tight">Pitch Coach</span>
      </Link>

      <nav
        aria-label="서비스 메뉴"
        className="mt-8 flex gap-2 overflow-x-auto lg:mt-12 lg:flex-col"
      >
        <Link
          to="/"
          className="whitespace-nowrap rounded-lg bg-cream px-3 py-2.5 text-sm font-bold"
        >
          내 Pitch
        </Link>
        <Link
          to="/takes"
          className="whitespace-nowrap rounded-lg px-3 py-2.5 text-sm text-stone hover:bg-cream hover:text-ink"
        >
          연습 기록
        </Link>
        <Link
          to="/about"
          className="whitespace-nowrap rounded-lg px-3 py-2.5 text-sm text-stone hover:bg-cream hover:text-ink"
        >
          이용 안내
        </Link>
      </nav>

      <div className="mt-8 text-sm text-stone lg:mt-auto">
        <p>{session.data?.name ?? '사용자'}</p>
        <button
          type="button"
          onClick={() => logout.mutate()}
          disabled={logout.isPending}
          className="mt-1 cursor-pointer text-left text-sm text-stone hover:text-ink disabled:cursor-wait disabled:opacity-60"
        >
          {logout.isPending ? '로그아웃 중…' : '로그아웃'}
        </button>
        {logout.isError && (
          <p role="alert" className="mt-2 text-sm text-ink">
            로그아웃에 실패했습니다. 다시 시도해 주세요.
          </p>
        )}
      </div>
    </aside>
  );
}

function PitchPreview({ index }: { index: number }) {
  const layout = index % 3;

  return (
    <div className="relative aspect-[1.62/1] overflow-hidden rounded-lg border border-line bg-panel p-5">
      {layout === 0 && (
        <div className="space-y-2.5">
          <span className="block h-2 w-2/3 rounded-full bg-cream" />
          <span className="block h-2 w-1/2 rounded-full bg-cream" />
          <span className="mt-5 block h-2 w-11/12 rounded-full bg-cream" />
          <span className="block h-2 w-5/6 rounded-full bg-cream" />
        </div>
      )}
      {layout === 1 && (
        <div className="grid h-full grid-cols-2 gap-3">
          <span className="rounded bg-cream" />
          <span className="rounded bg-cream" />
        </div>
      )}
      {layout === 2 && <span className="block h-2/5 w-full rounded bg-cream" />}
    </div>
  );
}

function formatDate(value: string | null) {
  if (!value) return null;

  const [year, month, day] = value.slice(0, 10).split('-');

  return year && month && day ? `${month}.${day}` : null;
}

function PitchCard({ pitch, index }: { pitch: HomeResponse['pitches'][number]; index: number }) {
  const date = formatDate(pitch.lastPracticedAt);
  const metadata = [`${Math.round(pitch.timeLimitSec / 60)}분`, `take ${pitch.takes.length}`];

  if (date) metadata.push(date);

  return (
    <article>
      <Link
        to={`/pitch/${pitch.id}/prepare`}
        className="group block rounded-xl focus-visible:outline-offset-4"
        aria-label={`${pitch.title} 카메라 점검 시작`}
      >
        <PitchPreview index={index} />
        <h2 className="mt-4 text-lg font-bold group-hover:text-coral-deep">{pitch.title}</h2>
        <p className="mt-2 font-mono text-sm text-stone">{metadata.join(' · ')}</p>
        {pitch.latestScore === null ? (
          <p className="mt-4 text-sm text-stone">첫 연습 · 점수 없음</p>
        ) : (
          <span className="mt-4 inline-flex rounded-full bg-coral-wash px-3 py-1.5 text-sm font-semibold text-coral-deep">
            최근 점수 {pitch.latestScore}
          </span>
        )}
      </Link>
    </article>
  );
}

function EmptyHome() {
  return (
    <section
      className="flex min-h-[60dvh] flex-col items-center justify-center text-center"
      aria-labelledby="empty-home-title"
    >
      <h2 id="empty-home-title" className="text-xl font-bold">
        아직 만든 Pitch가 없습니다
      </h2>
      <p className="mt-4 text-sm text-stone">발표 준비를 시작해볼까요?</p>
      <Link
        to="/pitch/new"
        className="mt-7 rounded-full bg-coral px-9 py-3.5 text-sm font-bold text-white hover:bg-coral-deep"
      >
        자료 올리기
      </Link>
      <p className="mt-8 flex items-center gap-2 bg-cream px-5 py-3 text-sm text-stone">
        <PitchCoachMark />첫 연습은 1번이면 충분해요
      </p>
    </section>
  );
}

function LoadingHome() {
  return (
    <div
      className="grid grid-cols-1 gap-8 md:grid-cols-2 xl:grid-cols-3"
      aria-label="Pitch 목록을 불러오는 중"
    >
      {Array.from({ length: 3 }, (_, index) => (
        <div key={index} className="animate-pulse">
          <div className="aspect-[1.62/1] rounded-lg bg-cream" />
          <div className="mt-4 h-6 w-2/3 rounded bg-cream" />
          <div className="mt-3 h-4 w-1/2 rounded bg-cream" />
        </div>
      ))}
    </div>
  );
}

function HomeContent() {
  const home = useHome();

  if (home.isPending) return <LoadingHome />;

  if (home.isError) {
    return (
      <section
        className="flex min-h-[60dvh] flex-col items-center justify-center text-center"
        role="alert"
      >
        <h2 className="text-xl font-bold">내 Pitch를 불러오지 못했습니다</h2>
        <p className="mt-3 text-sm text-stone">연결 상태를 확인한 뒤 다시 시도해 주세요.</p>
        <button
          type="button"
          onClick={() => void home.refetch()}
          className="mt-6 rounded-full border border-line-strong px-6 py-3 text-sm font-bold hover:bg-cream"
        >
          다시 시도
        </button>
      </section>
    );
  }

  if (home.data.pitches.length === 0) return <EmptyHome />;

  return (
    <div className="grid grid-cols-1 gap-x-8 gap-y-10 md:grid-cols-2 xl:grid-cols-3">
      {home.data.pitches.map((pitch, index) => (
        <PitchCard key={pitch.id} pitch={pitch} index={index} />
      ))}
    </div>
  );
}

/** P2: 로그인한 사용자의 Pitch와 다음 카메라 점검 진입점을 보여준다. */
export function HomePage() {
  useEffect(() => {
    document.title = '내 Pitch | 피치코치';
  }, []);

  return (
    <main className="flex min-h-dvh flex-col bg-greige lg:flex-row">
      <Sidebar />
      <section className="min-w-0 flex-1 px-6 py-10 sm:px-10 lg:px-16 lg:py-16">
        <div className="flex flex-wrap items-center justify-between gap-5">
          <h1 className="text-3xl font-extrabold tracking-tight sm:text-4xl">내 Pitch</h1>
          <Link
            to="/pitch/new"
            className="rounded-full bg-coral px-6 py-3.5 text-sm font-bold text-white hover:bg-coral-deep"
          >
            + 새 피치 만들기
          </Link>
        </div>
        <div className="mt-12">
          <HomeContent />
        </div>
      </section>
    </main>
  );
}
