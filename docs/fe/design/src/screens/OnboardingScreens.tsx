// 제품 웰컴(`frontend/src/features/onboarding/WelcomePage.tsx`) · 로그인(`features/auth/LoginPage.tsx`)의 설계서 샘플.
// 문구는 제품과 같다. 움직이는 장면(ComfortScene) · 넘어가는 숫자(FlipNumber)는 자리만 두고, 로그인은 Google로 가지 않는다.
import { useRef, type ReactNode } from 'react';
import { PitchCoachWordmark } from '@/ui/PitchCoachWordmark';
import type { Navigate } from './shared';

type ScreenProps = { stateId: string; onNavigate: Navigate };

const cta =
  'inline-flex items-center justify-center gap-6 border-2 border-ink bg-coral-deep px-6 py-3.5 font-bold text-white shadow-[4px_4px_0_var(--color-ink)] transition hover:-translate-y-1 hover:bg-ink active:translate-x-1 active:translate-y-1 active:shadow-none';

const SECTIONS = [
  ['service', '서비스 소개'],
  ['guide', '사용방법'],
  ['coaches', '연구실 소개'],
  ['faq', 'FAQ'],
  ['policies', '이용안내'],
] as const;

const STEPS = [
  [
    '자료를 챙겨요',
    '발표할 PDF 슬라이드와 대본을 준비해요. 슬라이드별로 대본을 확인하면 준비 끝.',
    'PDF + SCRIPT',
  ],
  [
    '내 페이스로 말해요',
    '마이크와 카메라를 점검하고 연습을 시작해요. 코칭을 참고하며 끝까지 말해봐요.',
    'YOUR FIRST TAKE',
  ],
  [
    '한 번 더 나아져요',
    '연습 결과를 확인하고 다음에 고칠 점을 찾아요. 자료를 다듬고, 다시 도전해요.',
    'ONE MORE TRY',
  ],
];

const FAQS = [
  [
    '무엇을 준비하면 되나요?',
    '발표 슬라이드 PDF, 대본, 마이크가 필요해요. 시선 코칭을 함께 이용하려면 카메라도 준비해 주세요. 노트북이나 데스크톱에서 이용하는 것을 권장해요.',
  ],
  [
    '발표가 처음이어도 괜찮나요?',
    '물론이에요. 대본을 보며 한 번 끝까지 말해보는 것부터 시작해 보세요. 치치도 한 번씩 연습하며 배우는 중이에요.',
  ],
  [
    '카메라 영상이 서버에 저장되나요?',
    '카메라 영상은 시선 분석을 위해 브라우저에서 처리하며 서버로 전송하지 않아요. 음성은 말하기 분석을 위해 서버로 전송되고, 시선 분석 결과와 연습 기록도 처리돼요. 아래 개인정보 안내에서 자세한 내용을 확인해 주세요.',
  ],
  [
    '어떤 브라우저에서 이용하나요?',
    '데스크톱의 최신 Chrome 또는 Edge를 권장해요. 연습 시작 전 마이크와 카메라 권한을 허용하고 장치 점검을 진행해 주세요.',
  ],
];

/** 제품 LaunchStart. 공개 전에는 "오픈 예정"으로 막고, 공개 뒤에는 로그인으로 간다 */
function Start({
  open,
  onStart,
  children,
}: {
  open: boolean;
  onStart: () => void;
  children: ReactNode;
}) {
  if (!open)
    return (
      <button type="button" disabled className={`${cta} cursor-not-allowed opacity-60`}>
        오픈 예정
      </button>
    );
  return (
    <button type="button" onClick={onStart} className={cta}>
      {children}
    </button>
  );
}

/** 웰컴(`/welcome`). 상태: default 공개 뒤 · before-launch 공개 전(카운트다운) */
export function WelcomeScreen({ stateId, onNavigate }: ScreenProps) {
  const open = stateId !== 'before-launch';
  const page = useRef<HTMLDivElement>(null);
  // 제품은 #id로 스크롤한다. 설계서 안의 링크는 화면 이동으로 바뀌므로 버튼으로 옮긴다
  const jump = (id: string) =>
    page.current?.querySelector(`#${id}`)?.scrollIntoView({ behavior: 'smooth' });
  const start = () => onNavigate('login');
  return (
    <div ref={page} className="bg-panel text-ink">
      <header className="sticky top-0 z-30 border-b border-line-strong bg-panel/95 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-5 px-6 py-5 lg:px-10">
          <PitchCoachWordmark />
          <nav
            aria-label="랜딩 페이지 메뉴"
            className="order-3 flex w-full justify-between gap-2 text-xs sm:order-none sm:ml-auto sm:w-auto sm:gap-8 sm:text-sm"
          >
            {SECTIONS.map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => jump(id)}
                className="inline-flex min-h-11 items-center border-b-2 border-transparent py-1 hover:text-ink"
              >
                {label}
              </button>
            ))}
          </nav>
          {open ? (
            <button
              type="button"
              onClick={start}
              className="border border-ink px-4 py-2 text-sm font-bold transition hover:bg-ink hover:text-panel"
            >
              시작하기 ↗
            </button>
          ) : (
            <button
              type="button"
              disabled
              className="cursor-not-allowed border border-ink px-4 py-2 text-sm font-bold opacity-60"
            >
              오픈 예정
            </button>
          )}
        </div>
      </header>

      <div className="mx-auto grid max-w-7xl items-center gap-12 px-6 pb-16 pt-12 lg:grid-cols-[1fr_1.1fr] lg:gap-8 lg:px-10 lg:pb-24 lg:pt-20">
        <div>
          <p className="mb-7 flex items-center gap-2 font-mono text-[10px] font-bold tracking-[.16em] sm:text-xs">
            <span className="h-2 w-2 bg-coral" /> WELCOME TO THE PRESENTATION LAB
          </p>
          <h1 className="text-5xl font-black leading-[1.22] tracking-[-.065em] sm:text-6xl xl:text-7xl">
            발표는 떨려도,
            <br />
            연습은 즐겁게.
          </h1>
          <p className="mt-9 text-base leading-8 sm:text-lg">
            조금 서툴러도 괜찮아. 치치도 처음이니까.
            <br />
            깐깐한 피코 교수의 코칭과 함께
            <br />
            다음 발표를 한 번 더 연습해요.
          </p>
          {!open && (
            <section
              aria-label="서비스 공개 안내"
              className="mt-8 border-2 border-ink bg-white p-4 sm:p-5"
            >
              <p className="font-bold">10월 30일 금요일 오후 2:00에 만나요.</p>
              <p className="mt-1 text-xs">한국 시간 기준</p>
              {/* 제품은 FlipNumber로 1초마다 넘어간다 */}
              <div role="timer" className="mt-4 grid grid-cols-4 gap-2">
                {['00', '17', '12', '45'].map((value, index) => (
                  <div key={index} className="min-w-0 text-center">
                    <span className="tabular block border-2 border-ink bg-ink py-2 font-mono text-3xl font-bold text-panel">
                      {value}
                    </span>
                    <span className="mt-3 block text-xs">{['일', '시간', '분', '초'][index]}</span>
                  </div>
                ))}
              </div>
            </section>
          )}
          <div className="mt-8 flex flex-wrap items-center gap-6">
            <Start open={open} onStart={start}>
              치치와 연습 시작하기 ↗
            </Start>
            <button
              type="button"
              onClick={() => jump('guide')}
              className="border-b border-ink py-1 text-sm"
            >
              어떻게 연습하나요? ↓
            </button>
          </div>
          <p className="mt-6 text-xs leading-6">
            PDF 슬라이드와 대본을 준비해 주세요.
            <br />
            카메라 영상은 브라우저 안에서 처리돼요.
          </p>
        </div>
        {/* 제품은 치치가 연습하는 움직이는 장면(ComfortScene)이다 */}
        <div className="flex aspect-[4/3] flex-col justify-between border-2 border-ink bg-cream p-5 shadow-[4px_4px_0_var(--color-ink)]">
          <p className="flex justify-between font-mono text-xs">
            <span>● LAB 01 / 치치의 첫 발표</span>
            <span>— □ ×</span>
          </p>
          <p className="landing-float w-fit max-w-[80%] border-2 border-ink bg-panel px-4 py-3 shadow-[3px_3px_0_var(--color-ink)]">
            <span className="mb-1 block font-mono text-[9px]">PROFESSOR / 오늘의 한마디</span>
            <span className="text-sm font-bold">괜찮습니다. 처음부터 잘하는 새는 없어요.</span>
          </p>
          <p className="text-center text-sm text-stone">움직이는 장면 자리</p>
          <p className="font-mono text-xs">조금씩, 나아지는 중입니다.</p>
        </div>
      </div>

      <div className="border-y-2 border-ink bg-ink px-6 py-4 text-panel">
        <div className="mx-auto flex max-w-6xl flex-wrap justify-between gap-3 font-mono text-[10px] tracking-wider sm:text-xs">
          <span>01. PREPARE YOUR PITCH</span>
          <span>02. FIND YOUR PACE</span>
          <span>03. TRY ONE MORE TIME</span>
        </div>
      </div>

      <section id="service" className="mx-auto max-w-7xl px-6 py-20 lg:px-10 lg:py-28">
        <p className="mb-3 font-mono text-xs font-bold">01 / A LITTLE HELP</p>
        <h2 className="text-3xl font-extrabold tracking-tight sm:text-4xl">
          혼자 연습해도,
          <br />
          혼자 고민하지 않도록.
        </h2>
        <p className="mt-5 text-sm leading-7">
          말하는 동안 놓치기 쉬운 습관들.
          <br />
          피코 교수가 짚어주면, 하나씩 연습해 봐요.
        </p>
        <p className="mt-8 border-2 border-dashed border-line-strong p-6 text-sm text-stone">
          코칭 예시(말하기 속도 · 시선 연습 · 시간 관리) 자리
        </p>
      </section>

      <section id="guide" className="border-y border-line-strong bg-greige px-6 py-20 lg:py-24">
        <div className="mx-auto max-w-6xl">
          <p className="mb-3 text-center font-mono text-xs font-bold">02 / HOW TO PLAY</p>
          <h2 className="text-center text-3xl font-extrabold sm:text-4xl">첫 연습까지, 세 걸음.</h2>
          <div className="mt-12 grid gap-8 md:grid-cols-3">
            {STEPS.map(([title, description, label], index) => (
              <article key={title} className="border-t-2 border-ink pt-6">
                <span className="font-mono text-5xl font-black text-coral-deep">0{index + 1}.</span>
                <h3 className="mb-3 mt-6 text-xl font-bold">{title}</h3>
                <p className="text-sm leading-7">{description}</p>
                <p className="mt-6 font-mono text-[10px] tracking-widest">{label} ↗</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section id="coaches" className="mx-auto max-w-7xl px-6 py-20 lg:px-10 lg:py-28">
        <p className="mb-3 font-mono text-xs font-bold">03 / THE PRESENTATION LAB</p>
        <h2 className="text-3xl font-extrabold leading-snug sm:text-4xl">
          작은 목소리도,
          <br />
          멀리 닿을 수 있도록.
        </h2>
        <p className="mt-8 border-2 border-dashed border-line-strong p-6 text-sm text-stone">
          연구실 소개 글 · 구성원(피코 교수 · 치치) 카드 자리
        </p>
      </section>

      <section id="faq" className="border-t border-line-strong px-6 py-20">
        <div className="mx-auto grid max-w-6xl gap-10 md:grid-cols-[.7fr_1.3fr]">
          <div>
            <p className="mb-3 font-mono text-xs font-bold">04 / BEFORE YOU START</p>
            <h2 className="text-3xl font-extrabold">
              잠깐, 궁금한 게<br />
              있다면.
            </h2>
          </div>
          <div>
            {FAQS.map(([question, answer]) => (
              <details key={question} className="group border-b border-line-strong py-5">
                <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-6 font-bold [&::-webkit-details-marker]:hidden">
                  {question}
                  <span aria-hidden="true" className="text-xl font-normal group-open:rotate-45">
                    +
                  </span>
                </summary>
                <p className="mt-4 pr-8 text-sm leading-7">{answer}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      <section className="border-y-2 border-ink bg-coral-wash px-6 py-16 text-center">
        <p className="font-mono text-xs tracking-widest">READY FOR YOUR FIRST TAKE?</p>
        <h2 className="mb-7 mt-4 text-3xl font-extrabold sm:text-4xl">
          잘하려고 왔잖아.
          <br className="sm:hidden" /> 이제 같이 해보자!
        </h2>
        <Start open={open} onStart={start}>
          첫 연습 시작하기 ↗
        </Start>
      </section>

      <section id="policies" className="mx-auto max-w-6xl px-6 py-12">
        <h2 className="font-bold">안심하고 연습하기 위한 약속</h2>
        <p className="mt-2 text-sm">이용 규칙과 데이터 처리 내용을 확인해 주세요.</p>
      </section>
    </div>
  );
}

// 제품 LoginPage의 authErrorMessage · loginStatusMessage. 상태 ID → 상태 줄 문구
const LOGIN_STATUS: Record<string, string | null> = {
  default: null,
  checking: '로그인 상태를 확인하고 있습니다.',
  'check-failed': '로그인 상태를 확인하지 못했습니다. 연결을 확인하고 다시 시도해 주세요.',
  redirecting: 'Google 로그인 화면으로 이동 중입니다.',
  cancelled: '로그인이 취소되었습니다. 원하시면 다시 로그인할 수 있습니다.',
  failed: '로그인을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.',
};

/** 로그인(`/login`). 웰컴과 같은 머리 · 테두리 · 그림자를 쓴다 */
export function LoginScreen({ stateId, onNavigate }: ScreenProps) {
  const message = LOGIN_STATUS[stateId] ?? null;
  const busy = stateId === 'checking' || stateId === 'redirecting';
  return (
    <div className="flex min-h-dvh flex-col bg-panel text-ink">
      <header className="border-b border-line-strong">
        <div className="mx-auto flex max-w-page flex-wrap items-center justify-between gap-3 px-6 py-4 lg:px-10">
          <button
            type="button"
            onClick={() => onNavigate('welcome')}
            aria-label="PITCH COACH · 서비스 소개로"
          >
            <PitchCoachWordmark />
          </button>
          <button
            type="button"
            onClick={() => onNavigate('welcome')}
            className="text-sm font-bold hover:text-ink"
          >
            ← 서비스 소개
          </button>
        </div>
      </header>
      <div className="flex flex-1 items-center justify-center px-6 py-12">
        <section
          aria-labelledby="login-title"
          className="w-full max-w-md border-2 border-ink bg-panel px-4 py-10 text-center shadow-[4px_4px_0_var(--color-ink)] [overflow-wrap:anywhere] sm:px-12"
        >
          <img
            src="/onboarding/chichi-bust.png"
            alt=""
            width={80}
            height={96}
            className="mx-auto h-24 w-20 object-contain [image-rendering:pixelated]"
          />
          <p className="mt-4 font-mono text-xs tracking-widest text-ink/80">
            READY FOR YOUR FIRST TAKE?
          </p>
          <h1 id="login-title" className="mt-2 text-2xl font-extrabold tracking-tight">
            로그인
          </h1>
          <p className="mt-3 text-sm leading-6">
            Google 계정으로 로그인하고
            <br />
            발표 연습을 시작하세요.
          </p>
          <button
            type="button"
            disabled={busy}
            onClick={() => onNavigate('home')}
            aria-label="Google 계정으로 로그인"
            className="mx-auto mt-8 flex min-h-11 max-w-full cursor-pointer items-center rounded disabled:cursor-wait"
          >
            <img
              src="/auth/google-sign-in.png"
              alt="Google 계정으로 로그인"
              className="h-10 w-auto max-w-full"
            />
          </button>
          <div role="status" aria-live="polite" className="mt-6 text-sm leading-6">
            {message && <p className="border border-line-strong bg-cream px-4 py-3">{message}</p>}
            {stateId === 'check-failed' && (
              <button type="button" className="mt-3 min-h-11 underline">
                연결 다시 확인
              </button>
            )}
          </div>
          <p className="mt-6 border-t border-line pt-5 text-xs leading-5 text-ink/80">
            로그인 전에{' '}
            <span className="font-bold text-ink underline underline-offset-4">이용약관</span>과{' '}
            <span className="font-bold text-ink underline underline-offset-4">
              개인정보 처리방침
            </span>
            을 확인해 주세요.
          </p>
        </section>
      </div>
    </div>
  );
}
