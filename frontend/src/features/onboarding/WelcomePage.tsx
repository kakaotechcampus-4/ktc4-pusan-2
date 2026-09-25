import { useEffect, useState, type MouseEvent } from 'react';
import { Link } from 'react-router';
import { PitchCoachWordmark } from './PitchCoachWordmark';

const cta =
  'inline-flex items-center justify-center gap-6 border-2 border-ink bg-coral px-6 py-3.5 font-bold text-white shadow-[4px_4px_0_var(--color-ink)] transition hover:-translate-y-1 hover:bg-coral-deep active:translate-x-1 active:translate-y-1 active:shadow-none';
const sections = [
  ['service', '서비스 소개'],
  ['guide', '사용방법'],
  ['coaches', '연구실 소개'],
  ['faq', 'FAQ'],
  ['policies', '이용안내'],
] as const;
const examples = [
  ['말하기 속도', '중요한 문장입니다. 한 박자 쉬어보세요.', '후우… 천천히, 다시 해볼게!'],
  ['시선 연습', '이번 문장은 앞을 보며 말해보세요.', '대본 말고, 앞을 보자…!'],
  ['시간 관리', '마무리할 시간입니다. 핵심을 정리하세요.', '좋아, 마지막 한 문장!'],
];
const faqs = [
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
export function WelcomePage() {
  const [active, setActive] = useState('');
  const [example, setExample] = useState(0);
  const [motion, setMotion] = useState(
    () => !matchMedia('(prefers-reduced-motion: reduce)').matches,
  );
  useEffect(() => {
    const destination = document.getElementById(window.location.hash.slice(1));
    destination?.scrollIntoView({ behavior: 'instant' });
    const preference = matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setMotion(!preference.matches);
    preference.addEventListener('change', update);
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) if (entry.isIntersecting) setActive(entry.target.id);
      },
      { rootMargin: '-15% 0px -50% 0px' },
    );
    sections.forEach(([id]) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    return () => {
      observer.disconnect();
      preference.removeEventListener('change', update);
    };
  }, []);
  function jump(event: MouseEvent<HTMLAnchorElement>, id: string) {
    const target = document.getElementById(id);
    if (!target) return;
    event.preventDefault();
    history.replaceState(null, '', `#${id}`);
    target.focus({ preventScroll: true });
    target.scrollIntoView({ behavior: motion ? 'smooth' : 'instant' });
    setActive(id);
  }
  return (
    <div className="landing min-h-dvh bg-panel text-ink" data-motion={motion ? 'on' : 'off'}>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:bg-panel focus:p-4"
      >
        본문으로 건너뛰기
      </a>
      <header className="sticky top-0 z-30 border-b border-line-strong bg-panel/95 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-6 py-4 lg:px-10">
          <a
            href="#main"
            onClick={(e) => jump(e, 'main')}
            aria-label="PITCH COACH · 피치코치 처음으로"
            className="flex items-center gap-3"
          >
            <PitchCoachWordmark />
          </a>
          <nav
            aria-label="랜딩 페이지 메뉴"
            className="order-3 flex w-full justify-between gap-3 text-xs sm:order-none sm:w-auto sm:gap-6 sm:text-sm"
          >
            {sections.map(([id, label]) => (
              <a
                key={id}
                href={`#${id}`}
                onClick={(e) => jump(e, id)}
                aria-current={active === id ? 'location' : undefined}
                className={`border-b-2 py-1 hover:text-coral-deep ${active === id ? 'border-coral text-coral-deep' : 'border-transparent'}`}
              >
                {label}
              </a>
            ))}
          </nav>
          <Link
            to="/login?next=/"
            className="border border-ink px-4 py-2 text-sm font-bold transition hover:bg-ink hover:text-panel"
          >
            시작하기 ↗
          </Link>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="scroll-mt-36">
        <section
          aria-labelledby="hero-title"
          className="relative mx-auto grid max-w-7xl items-center gap-12 px-6 pb-16 pt-12 lg:grid-cols-[1fr_1.1fr] lg:gap-8 lg:px-10 lg:pb-24 lg:pt-20"
        >
          <div>
            <p className="mb-7 flex items-center gap-2 font-mono text-[10px] font-bold tracking-[.16em] sm:text-xs">
              <span className="h-2 w-2 bg-coral" /> WELCOME TO THE PRESENTATION LAB
            </p>
            <h1
              id="hero-title"
              className="text-5xl font-black leading-[1.22] tracking-[-.065em] sm:text-6xl xl:text-7xl"
            >
              발표는 떨려도,
              <br />
              연습은{' '}
              <span className="relative inline-block text-coral-deep">
                즐겁게.
                <svg
                  viewBox="0 0 240 12"
                  className="absolute -bottom-3 left-0 w-full"
                  aria-hidden="true"
                >
                  <path
                    d="M0 8h45V4h65v4h70V4h60"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                </svg>
              </span>
            </h1>
            <p className="mt-9 text-base leading-8 sm:text-lg">
              조금 서툴러도 괜찮아. 치치도 처음이니까.
              <br />
              깐깐한 피코 교수의 코칭과 함께
              <br />
              다음 발표를 한 번 더 연습해요.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-6">
              <Link to="/login?next=/" className={cta}>
                치치와 연습 시작하기 ↗
              </Link>
              <a
                href="#guide"
                onClick={(e) => jump(e, 'guide')}
                className="border-b border-ink py-1 text-sm"
              >
                어떻게 연습하나요? ↓
              </a>
            </div>
            <p className="mt-6 text-xs leading-6">
              PDF 슬라이드와 대본을 준비해 주세요.
              <br />
              <span className="text-coral-deep">카메라 영상은 브라우저 안에서 처리돼요.</span>
            </p>
          </div>
          <div className="relative pt-4">
            <div className="absolute -right-1 top-0 z-10 rotate-6 border-2 border-ink bg-cream px-3 py-2 font-mono text-[10px] font-bold shadow-[3px_3px_0_var(--color-ink)]">
              PRACTICE MAKES BETTER!
            </div>
            <div className="border-2 border-ink bg-white shadow-[8px_8px_0_var(--color-line-strong)]">
              <div className="flex items-center justify-between border-b-2 border-ink bg-ink px-5 py-3 font-mono text-[10px] text-panel">
                <span>● LAB 01 / 치치의 첫 발표</span>
                <span>— □ ×</span>
              </div>
              <div className="relative overflow-hidden">
                <img
                  src="/onboarding/chichi-professor.png"
                  alt="눈물을 흘리지만 열심히 배우는 치치와 안경, 정장을 갖춘 피코 교수"
                  width="1254"
                  height="1254"
                  fetchPriority="high"
                  className="block aspect-[1.15] w-full object-cover"
                />
                <div className="landing-float absolute left-5 top-6 max-w-[80%] border-2 border-ink bg-panel px-4 py-3 shadow-[3px_3px_0_var(--color-ink)] sm:left-8 sm:top-8">
                  <p className="mb-1 font-mono text-[9px] text-coral-deep">
                    PROFESSOR / 오늘의 한마디
                  </p>
                  <p className="text-sm font-bold">괜찮습니다. 처음부터 잘하는 새는 없어요.</p>
                </div>
              </div>
              <div className="flex items-center gap-3 border-t border-line px-5 py-4 font-mono text-[10px]">
                <span>CONFIDENCE</span>
                <div className="flex flex-1 gap-1">
                  {Array.from({ length: 10 }, (_, i) => (
                    <span key={i} className={`h-2.5 flex-1 ${i < 6 ? 'bg-coral' : 'bg-line'}`} />
                  ))}
                </div>
                <span>+1 TAKE</span>
              </div>
            </div>
            <div className="mt-4 flex justify-between text-[11px]">
              <span>조금씩, 나아지는 중입니다.</span>
              <button
                onClick={() => setMotion(!motion)}
                aria-pressed={!motion}
                className="underline underline-offset-4"
              >
                {motion ? '모션 일시정지 Ⅱ' : '모션 재생 ▷'}
              </button>
            </div>
          </div>
        </section>
        <div className="border-y border-ink bg-ink px-6 py-4 text-panel">
          <div className="mx-auto flex max-w-6xl flex-wrap justify-between gap-3 font-mono text-[10px] tracking-wider sm:text-xs">
            <span>01. PREPARE YOUR PITCH</span>
            <span>✦</span>
            <span>02. FIND YOUR PACE</span>
            <span>✦</span>
            <span>03. TRY ONE MORE TIME</span>
          </div>
        </div>
        <section
          id="service"
          tabIndex={-1}
          className="mx-auto max-w-7xl scroll-mt-36 px-6 py-20 lg:px-10 lg:py-28"
        >
          <div className="mb-10 flex flex-wrap items-end justify-between gap-5">
            <div>
              <p className="mb-3 font-mono text-xs font-bold text-coral-deep">01 / A LITTLE HELP</p>
              <h2 className="text-3xl font-extrabold tracking-tight sm:text-4xl">
                혼자 연습해도,
                <br />
                혼자 고민하지 않도록.
              </h2>
            </div>
            <p className="text-sm leading-7">
              말하는 동안 놓치기 쉬운 습관들.
              <br />
              피코 교수가 짚어주면, 하나씩 연습해 봐요.
            </p>
          </div>
          <div className="grid border-2 border-ink lg:grid-cols-[.8fr_1.2fr]">
            <div className="bg-cream p-6 sm:p-9">
              <p className="mb-5 font-mono text-xs">COACHING PREVIEW · 코칭 예시</p>
              <div role="group" aria-label="코칭 예시 선택" className="space-y-3">
                {examples.map(([label], i) => (
                  <button
                    key={label}
                    aria-pressed={example === i}
                    onClick={() => setExample(i)}
                    className={`flex w-full items-center justify-between border px-5 py-4 text-left font-bold transition ${example === i ? 'border-ink bg-ink text-panel' : 'border-line-strong bg-panel hover:border-ink'}`}
                  >
                    <span>
                      <span className="mr-4 font-mono text-xs">0{i + 1}</span>
                      {label}
                    </span>
                    <span>↗</span>
                  </button>
                ))}
              </div>
              <p className="mt-5 text-xs leading-6">
                항목을 눌러 코칭을 미리 만나보세요.
                <br />
                실제 녹음이나 분석이 실행되지는 않아요.
              </p>
            </div>
            <div
              className="flex flex-col justify-center bg-panel p-6 sm:p-10"
              aria-live="polite"
              aria-atomic="true"
            >
              <p className="mb-3 font-mono text-[10px] text-coral-deep">피코 교수의 피드백</p>
              <p className="text-xl font-bold leading-9">“{examples[example][1]}”</p>
              <div
                className="my-8 flex h-16 items-center justify-center gap-2 border-y border-line py-3"
                aria-hidden="true"
              >
                {[30, 65, 45, 85, 55, 95, 60, 40, 70, 50, 35, 60].map((height, i) => (
                  <span
                    key={i}
                    className="landing-wave w-3 bg-coral sm:w-4"
                    style={{ height: `${height}%`, animationDelay: `${i * 90}ms` }}
                  />
                ))}
              </div>
              <p className="self-end border border-line-strong bg-cream px-5 py-3 text-sm">
                <span className="mr-3 font-bold">치치</span>
                {examples[example][2]}
              </p>
            </div>
          </div>
        </section>
        <section
          id="guide"
          tabIndex={-1}
          className="scroll-mt-36 border-y border-line-strong bg-greige px-6 py-20 lg:py-24"
        >
          <div className="mx-auto max-w-6xl">
            <p className="mb-3 text-center font-mono text-xs font-bold text-coral-deep">
              02 / HOW TO PLAY
            </p>
            <h2 className="text-center text-3xl font-extrabold sm:text-4xl">
              첫 연습까지, 세 걸음.
            </h2>
            <div className="mt-12 grid gap-8 md:grid-cols-3">
              {[
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
              ].map(([title, description, label], i) => (
                <article key={title} className="border-t-2 border-ink pt-6">
                  <span className="font-mono text-5xl font-black text-coral">0{i + 1}.</span>
                  <h3 className="mb-3 mt-6 text-xl font-bold">{title}</h3>
                  <p className="text-sm leading-7">{description}</p>
                  <p className="mt-6 font-mono text-[10px] tracking-widest">{label} ↗</p>
                </article>
              ))}
            </div>
          </div>
        </section>
        <section
          id="coaches"
          tabIndex={-1}
          className="mx-auto max-w-7xl scroll-mt-36 px-6 py-20 lg:px-10 lg:py-28"
        >
          <p className="mb-3 font-mono text-xs font-bold text-coral-deep">
            03 / THE PRESENTATION LAB
          </p>
          <div className="grid gap-7 border-b border-line-strong pb-10 lg:grid-cols-[.9fr_1.1fr] lg:gap-16">
            <div>
              <h2 className="text-3xl font-extrabold leading-snug sm:text-4xl">
                작은 목소리도,
                <br />
                멀리 닿을 수 있도록.
              </h2>
              <p className="mt-5 font-mono text-xs tracking-widest">PITCH COACH · 발표 연구실</p>
            </div>
            <div className="space-y-4 text-sm leading-8 sm:text-base">
              <p>
                하고 싶은 말은 많은데, 앞에 서면 부리부터 떨리는 새들이 있어요. 피치코치 발표
                연구실은 그런 새들이 자기 목소리를 찾아가는 곳이에요.
              </p>
              <p>
                깐깐한 피코 교수와 신입 연구원 치치는 매일 발표를 연습해요. 너무 빠르게 말한 날도,
                대본에서 눈을 떼지 못한 날도 모두 다음 연습을 위한 소중한 기록이 되죠.
              </p>
              <p className="font-bold">
                이곳의 연구 원칙은 하나. 완벽한 한 번보다, 어제보다 나아진 한 번.
              </p>
            </div>
          </div>
          <div className="mb-6 mt-10 flex items-center justify-between gap-4">
            <h3 className="text-xl font-bold">연구실 구성원</h3>
            <span className="font-mono text-[10px] tracking-widest">LAB MEMBERS / 02</span>
          </div>
          <div className="grid gap-6 md:grid-cols-2">
            {[true, false].map((professor) => (
              <article
                key={String(professor)}
                className={`border border-line-strong p-6 lg:p-8 ${professor ? 'bg-cream' : 'bg-coral-wash'}`}
              >
                <div className="mb-6 flex items-center gap-5">
                  <figure className="w-28 shrink-0 border border-ink bg-panel p-1.5 shadow-[3px_3px_0_var(--color-line-strong)] lg:w-32">
                    <img
                      src={
                        professor
                          ? '/onboarding/pico-portrait.png'
                          : '/onboarding/chichi-portrait-longsleeve.png'
                      }
                      alt={
                        professor
                          ? '안경과 정장을 갖춘 공작새 피코 교수의 픽셀 증명사진'
                          : '아이보리 깃털에 짙은 갈색 포인트, 흰색 Stüssy 롱슬리브를 입은 뱁새 치치의 픽셀 증명사진'
                      }
                      width={1254}
                      height={1254}
                      loading="lazy"
                      className="aspect-[3/4] w-full object-cover [image-rendering:pixelated]"
                    />
                    <figcaption className="pt-2 text-center font-mono text-[9px] tracking-widest">
                      {professor ? 'FACULTY / 001' : 'RESEARCHER / 002'}
                    </figcaption>
                  </figure>
                  <div>
                    <p className="font-mono text-[10px] tracking-widest">
                      {professor ? 'DIRECTOR' : 'JUNIOR RESEARCHER'}
                    </p>
                    <h4 className="mb-2 mt-3 text-2xl font-extrabold lg:text-3xl">
                      {professor ? '피코 교수' : '치치'}
                    </h4>
                    <p className="text-xs leading-6">
                      {professor ? '연구 책임자 · 공작새' : '신입 연구원 · 뱁새'}
                    </p>
                    <p className="mt-3 text-xs font-bold text-coral-deep">
                      {professor ? '연구 분야: 말하기와 시선' : '연구 과제: 떨려도 끝까지 말하기'}
                    </p>
                  </div>
                </div>
                <p className="text-sm leading-7">
                  {professor
                    ? '안경 너머로 놓치는 것 없이. 칭찬은 담백하게, 피드백은 정확하게. 좀처럼 웃지 않는 발표 연구실의 피코 교수. S등급을 받는 날에는, 그 미소를 볼 수 있을지도요.'
                    : '흰색 롱슬리브와 스트릿 패션을 좋아하는 뱁새. 평소엔 스웨그 넘치지만 발표 앞에선 살짝 버벅여요. 가끔 멍하고 자주 허둥대도, 연습만큼은 진심. 피코 교수의 피드백에 울고 웃으며 한 번 더 도전해요.'}
                </p>
                <p className="mt-6 border-t border-ink/20 pt-5 font-bold">
                  {professor
                    ? '“좋습니다. 한 번 더 해보죠.”'
                    : '“오늘은 어제보다 덜 떨릴지도! skrr”'}
                </p>
              </article>
            ))}
          </div>
        </section>
        <section
          id="faq"
          tabIndex={-1}
          className="scroll-mt-36 border-t border-line-strong px-6 py-20"
        >
          <div className="mx-auto grid max-w-6xl gap-10 md:grid-cols-[.7fr_1.3fr]">
            <div>
              <p className="mb-3 font-mono text-xs font-bold text-coral-deep">
                04 / BEFORE YOU START
              </p>
              <h2 className="text-3xl font-extrabold">
                잠깐, 궁금한 게<br />
                있다면.
              </h2>
            </div>
            <div>
              {faqs.map(([question, answer]) => (
                <details key={question} className="group border-b border-line-strong py-5">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-6 font-bold [&::-webkit-details-marker]:hidden">
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
          <Link to="/login?next=/" className={cta}>
            첫 연습 시작하기 ↗
          </Link>
        </section>
        <section id="policies" tabIndex={-1} className="mx-auto max-w-6xl scroll-mt-36 px-6 py-12">
          <div className="flex flex-wrap items-center justify-between gap-5">
            <div>
              <h2 className="font-bold">안심하고 연습하기 위한 약속</h2>
              <p className="mt-2 text-sm">이용 규칙과 데이터 처리 내용을 확인해 주세요.</p>
            </div>
            <div className="flex flex-wrap gap-5 text-sm">
              <Link className="underline underline-offset-4" to="/terms">
                이용약관 ↗
              </Link>
              <Link className="font-bold underline underline-offset-4" to="/privacy">
                개인정보 처리방침 ↗
              </Link>
            </div>
          </div>
          <p className="mt-5 text-xs leading-6">
            현재 정책 문서는 검토용 초안입니다. 운영 주체, 문의처, 보관기간 등은 서비스 공개 전에
            확정하여 안내할 예정입니다.
          </p>
        </section>
      </main>
      <footer className="border-t border-line-strong px-6 py-7">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4">
          <span className="font-mono text-xs font-bold">PITCH COACH / ONE TAKE AT A TIME.</span>
          <a
            href="#policies"
            onClick={(e) => jump(e, 'policies')}
            className="text-xs underline underline-offset-4"
          >
            이용약관 및 개인정보 안내 ↑
          </a>
          <span className="font-mono text-[10px]">© {new Date().getFullYear()} PITCH COACH</span>
        </div>
      </footer>
    </div>
  );
}
