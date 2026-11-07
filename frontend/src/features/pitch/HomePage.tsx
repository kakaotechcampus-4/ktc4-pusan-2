import { useEffect, useRef, useState } from 'react';
import { HomeSurveyNotice } from './HomeSurveyNotice';
import { DeletePitchDialog } from './DeletePitchDialog';
import type { HomePitch } from '@/types/home';
import { Link } from 'react-router';
import { HomeHelpDialog } from '@/shared/ui/HomeHelpDialog';
import { usePitchList } from '@/shared/api/home';
import { HomePitchCard } from './HomePitchCard';
import { duration, totalElapsed } from './homeFormat';
import { MAIN_ID } from '@/shared/ui/SkipLink';

const primary =
  'inline-flex items-center justify-center gap-5 border-2 border-ink bg-coral-deep px-5 py-3 text-sm font-bold text-white shadow-[4px_4px_0_var(--color-ink)] transition hover:-translate-y-0.5 hover:bg-ink active:translate-y-1 active:shadow-none';
export function HomePage() {
  const [helpOpen, setHelpOpen] = useState(false);
  const home = usePitchList();
  const data = home.data;
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
          <Link to="/pitch/new" className={primary}>
            <span aria-hidden="true">＋</span> 새 피치 만들기
          </Link>
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
        {/* 피치가 없으면 확인할 슬라이드 · 대본이 없어 TIP 을 빼고 빈 화면을 한 화면에 둡니다. 사용방법은 상단바에 있습니다 */}
        {data?.pitches.length !== 0 && (
          <aside className="mt-6 flex flex-wrap items-center justify-between gap-4 border-y border-line-strong py-3 text-sm lg:mt-5">
            <p>
              <span className="mr-3 tabular-nums text-coral-deep">TIP /</span>연습 전, 슬라이드와
              대본을 한 번 확인해 보세요.
            </p>
            <button
              type="button"
              onClick={() => setHelpOpen(true)}
              className="text-ink/80 hover:text-coral-deep"
            >
              사용방법 살펴보기
            </button>
          </aside>
        )}
      </main>
      <footer className="border-t border-line-strong">
        <div className="mx-auto flex max-w-page flex-wrap justify-between gap-4 px-6 py-4 text-xs text-ink/80 lg:px-10">
          <span className="tabular-nums tracking-widest">PITCH COACH · ONE TAKE AT A TIME.</span>
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
