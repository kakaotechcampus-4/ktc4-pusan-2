// 제품 홈 · 내 피치(`frontend/src/features/pitch/HomePage.tsx`)의 설계서 샘플.
import { useState } from 'react';
import { Link } from 'react-router';
import { Button } from '@/ui/Button';
import { HomeHelpDialog } from '@/ui/dialogs/HomeHelpDialog';
import { TestConsentDialog } from '@/ui/dialogs/TestConsentDialog';
import { HomePitchCard, type HomePitch } from '@/ui/HomePitchCard';
import { PITCH, type Navigate } from './shared';

const HOME_PITCHES: HomePitch[] = [
  { pitch_id: 'p1', pitch_title: PITCH, pitch_time: 300, thumbnail_url: null, takes: [1, 2] },
  {
    pitch_id: 'p2',
    pitch_title: '창업 경진대회 본선',
    pitch_time: 420,
    thumbnail_url: null,
    takes: [],
  },
];

const homePrimary =
  'inline-flex items-center justify-center gap-5 border-2 border-ink bg-coral-deep px-5 py-3 text-sm font-bold text-white shadow-[4px_4px_0_var(--color-ink)] transition hover:-translate-y-0.5 hover:bg-ink active:translate-y-1 active:shadow-none';

export function HomeScreen({ stateId, onNavigate }: { stateId: string; onNavigate: Navigate }) {
  const [helpOpen, setHelpOpen] = useState(false);
  const pitches = stateId === 'empty' ? [] : HOME_PITCHES;
  // 설문 안내 두 상태(#776)도 목록은 평소와 같다
  const ready = ['default', 'empty', 'consent', 'pending'].includes(stateId);
  const takeCount = pitches.reduce((sum, pitch) => sum + pitch.takes.length, 0);
  return (
    <div className="pitch-create flex min-h-[40rem] flex-col bg-greige text-ink">
      {stateId === 'consent' && <ConsentPrompt onAgree={() => onNavigate('home', 'default')} />}
      {stateId === 'pending' && <PendingSurveys onNavigate={onNavigate} />}
      <div className="mx-auto w-full max-w-page flex-1 px-6 pb-8 pt-8 lg:px-10">
        <div className="flex flex-wrap items-end justify-between gap-5">
          <div>
            <p className="mb-2 font-mono text-[10px] tracking-widest text-ink/80">MY PITCHES</p>
            <h1 className="text-3xl font-bold">내 피치</h1>
            <p className="mt-3 text-sm text-ink/80">
              {pitches.length && ready
                ? '제목을 누르면 자료와 대본을, Take 기록을 누르면 지금까지의 연습을 볼 수 있어요.'
                : '발표 자료와 지금까지의 연습 기록을 확인해 보세요.'}
            </p>
          </div>
          <Link to="/pitch/new" className={homePrimary}>
            <span aria-hidden="true">＋</span> 새 피치 만들기
          </Link>
        </div>
        {stateId === 'loading' && (
          <div
            role="status"
            className="mt-10 border border-line-strong bg-white p-10 text-center text-ink/80"
          >
            발표 노트를 가져오는 중이에요…
          </div>
        )}
        {stateId === 'error' && (
          <div role="alert" className="mt-10 border border-line-strong bg-white p-10 text-center">
            <h2 className="font-bold">발표 목록을 가져오지 못했어요.</h2>
            <p className="mt-2 text-sm text-ink/80">잠시 후 다시 시도해 주세요.</p>
            <button
              type="button"
              onClick={() => onNavigate('home', 'default')}
              className={`${homePrimary} mt-5`}
            >
              다시 불러오기
            </button>
          </div>
        )}
        {ready && pitches.length > 0 && (
          <div className="mt-5 grid grid-cols-1 divide-y divide-ink border border-ink bg-white sm:grid-cols-3 sm:divide-x sm:divide-y-0">
            <div className="px-5 py-3">
              <p className="text-xs text-ink/80">내 발표 노트</p>
              <p className="mt-1">
                <strong className="font-mono text-xl">
                  {String(pitches.length).padStart(2, '0')}
                </strong>
                <span className="ml-2 text-sm">개의 피치</span>
              </p>
            </div>
            <div className="px-5 py-3">
              <p className="text-xs text-ink/80">지금까지 쌓은 연습</p>
              <p className="mt-1">
                <strong className="font-mono text-xl">{String(takeCount).padStart(2, '0')}</strong>
                <span className="ml-2 text-sm">TAKES</span>
              </p>
            </div>
            <div className="px-5 py-3">
              <p className="text-xs text-ink/80">누적 발표 시간</p>
              <p className="mt-1 flex h-7 items-center gap-3 text-sm font-bold">09:12</p>
            </div>
          </div>
        )}
        {ready && (
          <section aria-label="발표 목록" className="mt-5">
            {pitches.length > 0 ? (
              <ul className="divide-y divide-line border-2 border-ink bg-white">
                {pitches.map((pitch, index) => (
                  <HomePitchCard key={pitch.pitch_id} pitch={pitch} index={index} />
                ))}
              </ul>
            ) : (
              <div className="border-2 border-dashed border-line-strong bg-white px-6 py-8 text-center">
                <p className="font-mono text-xs tracking-widest text-coral-deep">
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
        )}
        {/* 피치가 없으면 확인할 슬라이드 · 대본이 없어 TIP을 뺀다 */}
        {stateId !== 'empty' && (
          <aside className="mt-6 flex flex-wrap items-center justify-between gap-4 border-y border-line-strong py-3 text-sm lg:mt-5">
            <p>
              <span className="mr-3 font-mono text-coral-deep">TIP /</span>연습 전, 슬라이드와
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
      </div>
      {helpOpen && <HomeHelpDialog onDismiss={() => setHelpOpen(false)} />}
    </div>
  );
}

/**
 * 테스트 참여에 동의하지 않은 사용자의 홈 위쪽 카드 (제품 `HomeSurveyNotice.tsx`, #776).
 * 동의하면 카드가 사라진다. 동의한 뒤 끝난 Take 가 아직 없어서 남은 설문도 비어 있다.
 */
function ConsentPrompt({ onAgree }: { onAgree: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <section
      aria-label="사용자 테스트 참여"
      className="mx-auto mt-5 w-full max-w-page px-6 lg:px-10"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border border-dashed border-line-strong bg-white px-4 py-3 text-sm">
        <p>
          <span className="font-bold">사용자 테스트에 참여해 주세요.</span> 연습이 끝날 때마다 짧은
          설문으로 분석이 맞았는지 알려 주시면 됩니다.
        </p>
        <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>
          안내 보고 참여하기
        </Button>
      </div>
      {open && <TestConsentDialog onClose={() => setOpen(false)} onAgree={onAgree} />}
    </section>
  );
}

/** 동의한 뒤 끝났는데 설문을 남기지 않은 리포트. 누르면 그 리포트로 가고, 설문은 리포트 아래에서 한다 */
function PendingSurveys({ onNavigate }: { onNavigate: Navigate }) {
  const items = [
    { take: 'Take 2', pitch: PITCH },
    { take: 'Take 1', pitch: PITCH },
  ];
  return (
    <section aria-label="남은 설문" className="mx-auto mt-5 w-full max-w-page px-6 lg:px-10">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-2 border-ink bg-coral-wash px-4 py-3 text-sm">
        <p className="font-bold">설문을 기다리는 리포트가 {items.length}개 있어요.</p>
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {items.map((item) => (
            <li key={item.take}>
              <button
                type="button"
                onClick={() => onNavigate('report')}
                className="underline hover:text-coral-deep"
              >
                {item.pitch} · {item.take}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
