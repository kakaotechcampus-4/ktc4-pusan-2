// 제품 홈 · 내 피치(`frontend/src/features/pitch/HomePage.tsx`)의 설계서 샘플.
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { Button } from '@/ui/Button';
import { DeletePitchDialog } from '@/ui/dialogs/DeletePitchDialog';
import { HomeHelpDialog } from '@/ui/dialogs/HomeHelpDialog';
import { TestConsentDialog } from '@/ui/dialogs/TestConsentDialog';
import { HomePitchCard, type HomePitch } from '@/ui/HomePitchCard';
import { PITCH, type Navigate } from './shared';

const DEMO_PITCH: HomePitch = {
  pitch_id: 'demo-sample',
  pitch_title: '샘플 발표',
  pitch_time: 180,
  thumbnail_url: null,
  takes: [1],
  can_delete: true,
  is_demo: true,
};

const HOME_PITCHES: HomePitch[] = [
  { pitch_id: 'p1', pitch_title: PITCH, pitch_time: 300, thumbnail_url: null, takes: [1, 2] },
  {
    pitch_id: 'p2',
    pitch_title: '창업 경진대회 본선',
    pitch_time: 420,
    thumbnail_url: null,
    takes: [],
  },
  {
    pitch_id: 'p3',
    pitch_title: '스터디 중간 공유',
    pitch_time: 300,
    thumbnail_url: null,
    takes: [1],
    can_delete: false,
  },
];

/** 피치 삭제 시안(#807)의 상태. 메뉴 두 상태와 확인 창 세 상태 */
const DELETE_STATES = [
  'delete-menu',
  'delete-blocked',
  'delete-confirm',
  'delete-pending',
  'delete-error',
];
const DIALOG_STATES: Record<string, { busy: boolean; error: 'failed' | null }> = {
  'delete-confirm': { busy: false, error: null },
  'delete-pending': { busy: true, error: null },
  'delete-error': { busy: false, error: 'failed' },
};

/** 튜토리얼 진입을 권할 때 쓰는 PRESS START 스티커(#1105) — 제품과 같은 별 좌표. */
const STARBURST =
  '50,2 57.6,11.1 68.4,5 71.7,15.8 83.9,13.7 82.4,24.5 94.3,26.7 88.3,35.9 98,42 88.3,48.1 ' +
  '94.3,57.3 82.4,59.5 83.9,70.3 71.7,68.2 68.4,79 57.6,72.9 50,82 42.4,72.9 31.6,79 28.3,68.2 ' +
  '16.1,70.3 17.6,59.5 5.7,57.3 11.7,48.1 2,42 11.7,35.9 5.7,26.7 17.6,24.5 16.1,13.7 28.3,15.8 ' +
  '31.6,5 42.4,11.1';

const homePrimary =
  'inline-flex items-center justify-center gap-5 border-2 border-ink bg-coral-deep px-5 py-3 text-sm font-bold text-white shadow-[4px_4px_0_var(--color-ink)] transition hover:-translate-y-0.5 hover:bg-ink active:translate-y-1 active:shadow-none';

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
  '왼쪽 아래 채팅 에이전트에게 지난 Take 기록이나 발표 방법을 물어보세요.',
];

const HOME_TIP_INTERVAL_MS = 8000;

export function HomeScreen({ stateId, onNavigate }: { stateId: string; onNavigate: Navigate }) {
  const [agreed, setAgreed] = useState(false);
  const consentStatus = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (agreed) consentStatus.current?.focus();
  }, [agreed]);
  const [helpOpen, setHelpOpen] = useState(false);
  const [tipIndex, setTipIndex] = useState(0);
  const [tipPaused, setTipPaused] = useState(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true,
  );
  const [tipHovered, setTipHovered] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
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
  // 기본 상태에서 메뉴로 지우면 그 줄만 이 미리보기에서 뺀다. 서버 요청은 없다
  const [removed, setRemoved] = useState<string[]>([]);
  const [asking, setAsking] = useState<string | null>(null);
  const samplePitches = stateId === 'sample' ? [...HOME_PITCHES, DEMO_PITCH] : HOME_PITCHES;
  const pitches =
    stateId === 'empty' ? [] : samplePitches.filter((pitch) => !removed.includes(pitch.pitch_id));
  const showPrepare =
    ['default', 'sample', 'consent', 'pending', 'prepare-entry-draft'].includes(stateId) ||
    DELETE_STATES.includes(stateId);
  const dialogState = DIALOG_STATES[stateId];
  const target = HOME_PITCHES.find((pitch) => pitch.pitch_id === (asking ?? 'p1'))!;
  // 설문 안내 두 상태(#776)와 발표 준비 버튼 시안도 목록은 평소와 같다
  const ready = ['default', 'empty', 'consent', 'pending'].includes(stateId) || showPrepare;
  const takeCount = pitches.reduce((sum, pitch) => sum + pitch.takes.length, 0);
  return (
    <div className="pitch-create flex min-h-[40rem] flex-col bg-greige text-ink">
      {stateId === 'consent' &&
        (agreed ? (
          <div className="mx-auto mt-5 w-full max-w-page px-6 lg:px-10">
            <p
              ref={consentStatus}
              role="status"
              tabIndex={-1}
              className="border-2 border-ink bg-coral-wash px-4 py-3 text-sm"
            >
              <span className="block font-bold">사용자 테스트 참여에 동의했어요.</span>
              <span className="block">설문은 각 리포트에서 남길 수 있어요.</span>
            </p>
          </div>
        ) : (
          <ConsentPrompt onAgree={() => setAgreed(true)} />
        ))}
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
          <div className="flex flex-wrap items-center gap-3">
            {/* 정적 샘플은 아직 튜토리얼을 마치지 않은 계정의 강조 상태를 나타낸다. */}
            <span className="relative inline-flex">
              <Link
                to="/pitch/tutorial"
                className="tutorial-invite inline-flex items-center justify-center border-2 border-ink bg-panel py-3 pr-5 pl-10 text-sm font-bold hover:bg-cream"
              >
                튜토리얼 해 보기 →
              </Link>
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
            </span>
            <Link to="/pitch/new" className={homePrimary}>
              <span aria-hidden="true">＋</span> 새 피치 만들기
            </Link>
          </div>
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
                  <HomePitchCard
                    key={pitch.pitch_id}
                    pitch={pitch}
                    index={index}
                    showPrepare={showPrepare}
                    menuOpen={
                      (stateId === 'delete-menu' && pitch.pitch_id === 'p1') ||
                      (stateId === 'delete-blocked' && pitch.pitch_id === 'p3')
                    }
                    onDelete={() => setAsking(pitch.pitch_id)}
                  />
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
      </div>
      <footer className="border-t border-line-strong">
        <div className="home-footer-content mx-auto flex w-full max-w-page flex-wrap items-center gap-x-4 gap-y-2 px-6 py-3 text-xs text-ink/80 xl:flex-nowrap xl:px-10">
          <div className="flex min-h-9 min-w-0 basis-full flex-wrap items-center gap-x-2 lg:min-w-0 lg:flex-1 lg:basis-auto lg:flex-nowrap">
            <span className="shrink-0 font-mono text-coral-deep">TIP /</span>
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
              className="min-h-9 shrink-0 px-1 text-xs underline underline-offset-4 hover:text-coral-deep focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
            >
              {tipPaused ? '재개' : '일시정지'}
            </button>
          </div>
          <button
            type="button"
            onClick={() => setHelpOpen(true)}
            className="min-h-9 shrink-0 whitespace-nowrap hover:text-coral-deep focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
          >
            사용방법 살펴보기
          </button>
          <span className="hidden shrink-0 whitespace-nowrap font-mono tracking-widest 2xl:inline">
            PITCH COACH · ONE TAKE AT A TIME.
          </span>
          <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1">
            <Link to="/terms?from=home" className="whitespace-nowrap hover:text-ink">
              이용약관
            </Link>
            <Link to="/privacy?from=home" className="whitespace-nowrap hover:text-ink">
              개인정보 처리방침
            </Link>
          </div>
        </div>
      </footer>
      {helpOpen && <HomeHelpDialog onDismiss={() => setHelpOpen(false)} />}
      {(dialogState || asking) && (
        <DeletePitchDialog
          pitchTitle={target.pitch_title}
          takeCount={target.takes.length}
          busy={dialogState?.busy ?? false}
          error={dialogState?.error ?? null}
          onConfirm={() => {
            setRemoved((ids) => [...ids, target.pitch_id]);
            setAsking(null);
            if (dialogState) onNavigate('home', 'default');
          }}
          onCancel={() => {
            setAsking(null);
            if (dialogState) onNavigate('home', 'default');
          }}
        />
      )}
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
