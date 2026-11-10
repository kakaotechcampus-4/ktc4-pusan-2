import { useEffect, useRef, useState } from 'react';
import { HomeSurveyNotice } from './HomeSurveyNotice';
import { DeletePitchDialog } from './DeletePitchDialog';
import type { HomePitch } from '@/types/home';
import { Link } from 'react-router';
import { HomeHelpDialog } from '@/shared/ui/HomeHelpDialog';
import { usePitchList } from '@/shared/api/home';
import { useSession } from '@/shared/api/useSession';
import { HomePitchCard } from './HomePitchCard';
import { invitesTutorial } from './tutorial/tutorialInvite';
import { duration, totalElapsed } from './homeFormat';
import { MAIN_ID } from '@/shared/ui/SkipLink';

const primary =
  'inline-flex items-center justify-center gap-5 border-2 border-ink bg-coral-deep px-5 py-3 text-sm font-bold text-white shadow-[4px_4px_0_var(--color-ink)] transition hover:-translate-y-0.5 hover:bg-ink active:translate-y-1 active:shadow-none';
/** 튜토리얼 초대 스티커의 16꼭지 별 (viewBox 0 0 100 84, 가로로 조금 넓게) */
const STARBURST =
  '50,2 57.6,11.1 68.4,5 71.7,15.8 83.9,13.7 82.4,24.5 94.3,26.7 88.3,35.9 98,42 88.3,48.1 ' +
  '94.3,57.3 82.4,59.5 83.9,70.3 71.7,68.2 68.4,79 57.6,72.9 50,82 42.4,72.9 31.6,79 28.3,68.2 ' +
  '16.1,70.3 17.6,59.5 5.7,57.3 11.7,48.1 2,42 11.7,35.9 5.7,26.7 17.6,24.5 16.1,13.7 28.3,15.8 ' +
  '31.6,5 42.4,11.1';
const HOME_TIPS = [
  '연습 전, 슬라이드와 대본을 한 번 확인해 보세요.',
  '시작 전 세팅에서 대본 표시 방식을 선택할 수 있어요.',
  '첫 연습에서는 한 가지 목표에 집중해 보세요.',
  '연습 후에는 Take 리포트에서 지난 연습과 달라진 점을 확인해 보세요.',
  '발표 중에는 슬라이드를 넘기며 연습할 수 있어요.',
  '사용방법 살펴보기에서 연습 순서를 확인할 수 있어요.',
  '발표가 막혔다면, 잠깐 멈추고 다시 시작해 보세요.',
  '좋은 발표는 한 번에 완성되지 않아요. Take를 쌓아 보세요.',
  '청중을 바라보는 연습도 발표 실력의 일부예요.',
  '지난 Take보다 한 가지만 나아져도 충분한 발전이에요.',
  '오른쪽 아래 새미나이 버튼을 누르면 오른쪽에 대화가 열려요. 지난 Take 기록이나 발표 방법을 물어보세요.',
];

const HOME_TIP_INTERVAL_MS = 8000;

// 홈은 채팅 기능을 가져올 수 없으므로 움직임 감소 설정만 여기서 읽습니다.
const initiallyPauseTips = () =>
  typeof globalThis.matchMedia === 'function' &&
  globalThis.matchMedia('(prefers-reduced-motion: reduce)').matches;

export function HomePage() {
  const [helpOpen, setHelpOpen] = useState(false);
  const [tipIndex, setTipIndex] = useState(0);
  const [tipPaused, setTipPaused] = useState(initiallyPauseTips);
  const [tipHovered, setTipHovered] = useState(false);
  const home = usePitchList();
  const data = home.data;
  // 튜토리얼 리포트를 받았는지는 내 정보에 있습니다. 로그인 확인(`RequireSession`)이 이미 받아 둔 값입니다
  const reportReceived = useSession().data?.tutorial_report_at != null;
  // 목록을 받은 뒤에만 정합니다. 받는 중 · 실패에 움직이면 이미 해 본 사람에게도 잠깐 권하게 됩니다
  const inviteTutorial = data !== undefined && invitesTutorial(data.pitches, reportReceived);
  const count = data?.pitches.reduce((sum, pitch) => sum + pitch.takes.length, 0) ?? 0;
  const listRef = useRef<HTMLUListElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  /** 지울지 묻는 피치. `opener` 는 메뉴를 연 ··· 버튼입니다 (메뉴가 닫히며 초점을 돌려 둡니다) */
  const [deleting, setDeleting] = useState<{
    pitch: HomePitch;
    index: number;
    opener: HTMLElement | null;
  } | null>(null);
  const [deleted, setDeleted] = useState('');
  /** 창을 내린 뒤 초점을 둘 곳. 창이 문서에서 빠진 다음(아래 effect)에 옮깁니다 */
  const focusAfter = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (deleting) return;
    focusAfter.current?.();
    focusAfter.current = null;
  }, [deleting]);

  useEffect(() => {
    if (typeof globalThis.matchMedia !== 'function') return;
    const preference = globalThis.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setTipPaused(preference.matches);
    preference.addEventListener('change', update);
    return () => preference.removeEventListener('change', update);
  }, []);

  useEffect(() => {
    if (tipPaused || tipHovered) return;
    const timer = window.setInterval(
      () => setTipIndex((current) => (current + 1) % HOME_TIPS.length),
      HOME_TIP_INTERVAL_MS,
    );
    return () => window.clearInterval(timer);
  }, [tipPaused, tipHovered]);

  const cancelDelete = () => {
    const opener = deleting?.opener;
    focusAfter.current = () => opener?.focus();
    setDeleting(null);
  };
  // 지운 줄의 ··· 는 사라졌습니다. 같은 자리에 온 다음 줄(마지막이었으면 윗줄), 줄이 없으면 제목으로 옮깁니다.
  // 목록 알림(setQueryData)과 창 닫기는 따로 그려져 지운 줄이 아직 남아 있을 수 있어, 지운 id 의 줄은 걸러서 고릅니다
  const finishDelete = () => {
    if (!deleting) return;
    const { index, pitch } = deleting;
    focusAfter.current = () => {
      const rows = [
        ...(listRef.current?.querySelectorAll<HTMLElement>(':scope > li') ?? []),
      ].filter((row) => row.dataset.pitchId !== pitch.pitch_id);
      const row = rows[Math.min(index, rows.length - 1)];
      (row?.querySelector<HTMLElement>('a, button') ?? headingRef.current)?.focus();
    };
    setDeleted(`‘${pitch.pitch_title}’ 피치를 삭제했어요.`);
    setDeleting(null);
  };
  return (
    // 상위 min-h-dvh flex 셸에서 남은 높이를 채우되, 피치가 많아지면 페이지 전체가 자연스럽게 늘어납니다.
    <div className="pitch-create flex flex-1 flex-col bg-greige text-ink">
      <HomeSurveyNotice />
      <main
        id={MAIN_ID}
        tabIndex={-1}
        className="mx-auto w-full max-w-page flex-1 px-6 pb-8 pt-8 lg:px-10"
      >
        <div className="flex flex-wrap items-end justify-between gap-5">
          <div>
            <p className="mb-2 tabular-nums text-[10px] tracking-widest text-ink/80">MY PITCHES</p>
            <h1 ref={headingRef} tabIndex={-1} className="text-3xl font-bold">
              내 피치
            </h1>
            <p className="mt-3 text-sm text-ink/80">
              {/* 목록 위 안내를 여기로 올려 목록을 길게 씁니다. 피치가 없으면 가리킬 줄이 없어 처음 문구를 둡니다 */}
              {data?.pitches.length
                ? '제목을 누르면 자료와 대본을, Take 기록을 누르면 지금까지의 연습을 볼 수 있어요.'
                : '발표 자료와 지금까지의 연습 기록을 확인해 보세요.'}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {/* 샘플로 끝까지 연습하지 않은 사람에게만 버튼이 톡톡 튀고 스티커가 붙습니다 (index.css `.tutorial-invite`) */}
            <span className="relative inline-flex">
              <Link
                to="/pitch/tutorial"
                className={`inline-flex items-center justify-center border-2 border-ink bg-panel py-3 pr-5 text-sm font-bold hover:bg-cream ${inviteTutorial ? 'tutorial-invite pl-10' : 'pl-5'}`}
              >
                튜토리얼 해 보기 →
              </Link>
              {inviteTutorial && (
                // 버튼 왼쪽 위 모서리에 걸친 16꼭지 별 스티커. 버튼 안쪽까지 걸쳐서 붙을 때는 버튼 왼쪽 여백을
                // 넓혀(pl-10) 글자를 비켜 둡니다. 톡톡 튀는 버튼과 같이 레트로 게임의
                // "PRESS START" 를 떠올리게 합니다. 꾸밈이라 읽지 않습니다(버튼 이름이 할 일을 말합니다)
                <span
                  aria-hidden="true"
                  data-tutorial-sticker=""
                  className="pointer-events-none absolute -top-7 -left-8 h-[60px] w-[72px] -rotate-12"
                >
                  <svg viewBox="0 0 100 84" className="absolute inset-0 h-full w-full">
                    <polygon
                      points={STARBURST}
                      className="fill-coral-deep stroke-ink"
                      strokeWidth="3.5"
                      strokeLinejoin="miter"
                    />
                  </svg>
                  <span className="absolute inset-0 flex flex-col items-center justify-center text-[11px] font-extrabold leading-[1.1] tracking-tight text-white">
                    <span>PRESS</span>
                    <span>START!</span>
                  </span>
                </span>
              )}
            </span>
            <Link to="/pitch/new" className={primary}>
              <span aria-hidden="true">＋</span> 새 피치 만들기
            </Link>
          </div>
        </div>
        {home.isPending && (
          <div
            role="status"
            className="mt-10 border border-line-strong bg-white p-10 text-center text-ink/80"
          >
            발표 노트를 가져오는 중이에요…
          </div>
        )}
        {/* 불러오는 중에는 isError 도 data 도 없어 아래는 그리지 않습니다 */}
        {home.isError ? (
          <div role="alert" className="mt-10 border border-line-strong bg-white p-10 text-center">
            <h2 className="font-bold">발표 목록을 가져오지 못했어요.</h2>
            <p className="mt-2 text-sm text-ink/80">잠시 후 다시 시도해 주세요.</p>
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
              {/* 피치가 없으면 셋 다 0 · — 이라 빼고, 빈 화면이 스크롤 없이 한 화면에 들어가게 합니다 */}
              {data.pitches.length > 0 && (
                <div className="mt-5 grid grid-cols-1 divide-y divide-ink border border-ink bg-white sm:grid-cols-3 sm:divide-x sm:divide-y-0">
                  <div className="px-5 py-3">
                    <p className="text-xs text-ink/80">내 발표 노트</p>
                    <p className="mt-1">
                      <strong className="tabular-nums text-xl">
                        {data.pitches.length.toString().padStart(2, '0')}
                      </strong>
                      <span className="ml-2 text-sm">개의 피치</span>
                    </p>
                  </div>
                  <div className="px-5 py-3">
                    <p className="text-xs text-ink/80">지금까지 쌓은 연습</p>
                    <p className="mt-1">
                      <strong className="tabular-nums text-xl">
                        {count.toString().padStart(2, '0')}
                      </strong>
                      <span className="ml-2 text-sm">TAKES</span>
                    </p>
                  </div>
                  <div className="px-5 py-3">
                    <p className="text-xs text-ink/80">누적 발표 시간</p>
                    <p className="mt-1 flex h-7 items-center gap-3 text-sm font-bold">
                      {duration(totalElapsed(data.pitches.flatMap((pitch) => pitch.takes)))}
                    </p>
                  </div>
                </div>
              )}
              {/* 피치 목록의 높이를 제한하지 않아 페이지 스크롤로 모든 피치를 탐색합니다. */}
              <section aria-label="발표 목록" className="mt-5">
                {data.pitches.length ? (
                  <ul ref={listRef} className="divide-y divide-line border-2 border-ink bg-white">
                    {data.pitches.map((pitch, index) => (
                      <HomePitchCard
                        key={pitch.pitch_id}
                        pitch={pitch}
                        index={index}
                        onDelete={(target) =>
                          setDeleting({
                            pitch: target,
                            index,
                            opener: document.activeElement as HTMLElement | null,
                          })
                        }
                      />
                    ))}
                  </ul>
                ) : (
                  <div className="border-2 border-dashed border-line-strong bg-white px-6 py-8 text-center">
                    <p className="tabular-nums text-xs tracking-widest text-coral-deep">
                      YOUR FIRST PITCH
                    </p>
                    <h3 className="mt-4 text-2xl font-bold">아직은 빈 노트, 곧 멋진 발표.</h3>
                    <p className="mt-3 text-sm leading-7 text-ink/80">
                      슬라이드 PDF와 대본을 준비해 주세요.
                      <br />
                      치치와 함께 첫 번째 연습을 시작해 봐요.
                    </p>
                  </div>
                )}
              </section>
              {/* 지운 줄은 말없이 사라져서, 화면 낭독기에는 지웠다고 알립니다 */}
              <p role="status" className="sr-only">
                {deleted}
              </p>
            </>
          )
        )}
      </main>
      <footer className="border-t border-line-strong">
        <div className="home-footer-content mx-auto flex w-full max-w-page flex-wrap items-center gap-x-4 gap-y-2 px-6 py-3 text-xs text-ink/80 xl:flex-nowrap xl:px-10">
          <div className="flex min-h-9 min-w-0 basis-full flex-wrap items-center gap-x-2 lg:min-w-0 lg:flex-1 lg:basis-auto lg:flex-nowrap">
            <span className="shrink-0 tabular-nums text-coral-deep">TIP /</span>
            <p
              title={HOME_TIPS[tipIndex]}
              onMouseEnter={() => setTipHovered(true)}
              onMouseLeave={() => setTipHovered(false)}
              className="order-3 min-h-20 w-full min-w-0 whitespace-normal leading-5 sm:min-h-10 lg:order-none lg:min-h-0 lg:w-auto lg:flex-1 lg:truncate"
            >
              {HOME_TIPS[tipIndex]}
            </p>
            <button
              type="button"
              onClick={() => setTipPaused((current) => !current)}
              aria-label={tipPaused ? '팁 자동 전환 재개' : '팁 자동 전환 일시정지'}
              title={tipPaused ? '팁 자동 전환 재개' : '팁 자동 전환 일시정지'}
              className="inline-flex size-11 shrink-0 items-center justify-center hover:bg-ink/10 hover:text-coral-deep focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                className="size-5"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                {tipPaused ? <path d="m8 5 11 7-11 7V5Z" /> : <path d="M9 5v14m6-14v14" />}
              </svg>
            </button>
          </div>
          <button
            type="button"
            onClick={() => setHelpOpen(true)}
            className="min-h-9 shrink-0 whitespace-nowrap hover:text-coral-deep focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
          >
            사용방법 살펴보기
          </button>
          <span className="hidden shrink-0 whitespace-nowrap tabular-nums tracking-widest 2xl:inline">
            PITCH COACH · ONE TAKE AT A TIME.
          </span>
          <nav
            aria-label="서비스 정책"
            className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1"
          >
            <Link to="/terms?from=home" className="whitespace-nowrap hover:text-ink">
              이용약관
            </Link>
            <Link to="/privacy?from=home" className="whitespace-nowrap hover:text-ink">
              개인정보 처리방침
            </Link>
          </nav>
        </div>
      </footer>
      {helpOpen && <HomeHelpDialog onDismiss={() => setHelpOpen(false)} />}
      {deleting && (
        <DeletePitchDialog
          pitch={deleting.pitch}
          onCancel={cancelDelete}
          onDeleted={finishDelete}
        />
      )}
    </div>
  );
}
