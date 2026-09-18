import { Link } from 'react-router';

function PitchCoachMark() {
  return (
    <svg aria-hidden="true" className="h-7 w-9" viewBox="0 0 34 26" shapeRendering="crispEdges">
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

function ProductPreview() {
  return (
    <figure className="relative isolate aspect-[1000/620] w-full max-w-5xl">
      {/* Match the SVG opening; keep the screen image replaceable at its natural ratio. */}
      <img
        src="/onboarding/rehearsal-preview.png"
        alt="노트북에 표시된 피치코치 리허설 화면 예시"
        width={3974}
        height={2478}
        className="absolute left-[10.2%] top-[3.5484%] block h-auto w-[79.6%]"
      />
      <img
        src="/onboarding/laptop-frame.svg"
        alt=""
        aria-hidden="true"
        width={1000}
        height={620}
        className="pointer-events-none relative block w-full select-none"
      />
    </figure>
  );
}

/** P1: 첫 방문자에게 서비스의 핵심 가치와 시작점을 안내한다. */
export function WelcomePage() {
  return (
    <main className="min-h-dvh overflow-hidden bg-greige px-4 pb-0 pt-4 sm:px-8 sm:pt-7">
      <header className="mx-auto flex h-14 max-w-[1440px] items-center justify-between rounded-full bg-cream px-5 sm:h-15 sm:px-7">
        <Link
          to="/welcome"
          className="flex items-center gap-2 font-bold tracking-tight"
          aria-label="피치코치 홈"
        >
          <PitchCoachMark />
          <span>Pitch Coach</span>
        </Link>
        <nav aria-label="주요 메뉴" className="flex items-center gap-5 text-sm sm:gap-7">
          <a href="#home" className="hidden font-medium sm:inline">
            홈
          </a>
          <a href="#service" className="hidden text-stone sm:inline">
            서비스
          </a>
          {/* 가격 정책과 고객센터 섹션을 추가할 때 다시 표시합니다.
          <a href="#pricing" className="hidden text-stone sm:inline">
            가격 정책
          </a>
          <a href="#support" className="hidden text-stone lg:inline">
            고객센터
          </a> */}
          <Link
            to="/login?next=/"
            className="rounded-full bg-coral px-5 py-2.5 text-sm font-bold text-white transition-colors hover:bg-coral-deep"
          >
            로그인
          </Link>
        </nav>
      </header>

      <section
        id="home"
        className="mx-auto flex max-w-5xl flex-col items-center pt-12 text-center sm:pt-18"
      >
        <p className="inline-flex items-center gap-2 rounded-full bg-coral-wash px-3.5 py-2 text-xs font-medium text-coral-deep">
          <svg
            aria-hidden="true"
            className="h-3.5 w-3"
            viewBox="0 0 12 14"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <path d="M6 1 1 3v4.2C1 10 3.2 12.3 6 13c2.8-.7 5-3 5-5.8V3L6 1Z" />
          </svg>
          얼굴 영상은 브라우저 밖으로 나가지 않습니다
        </p>
        <h1 className="mt-5 text-4xl font-extrabold leading-tight tracking-tight sm:mt-6 sm:text-6xl">
          떨리는 발표, 혼자서도
          <br />
          제대로 연습할 수 있어요
        </h1>
        <p id="service" className="mt-5 text-base text-stone sm:text-lg">
          말하기와 시선을 함께 봐주는 AI 발표 코치
        </p>
        <Link
          to="/login?next=/"
          className="mt-7 rounded-full bg-coral px-9 py-4 text-base font-bold text-white shadow-sm transition-colors hover:bg-coral-deep"
        >
          무료로 시작
        </Link>
      </section>

      <section
        aria-label="피치코치 연습 화면 미리보기"
        className="mx-auto mt-12 flex max-w-5xl justify-center sm:mt-16"
      >
        <ProductPreview />
      </section>
    </main>
  );
}
