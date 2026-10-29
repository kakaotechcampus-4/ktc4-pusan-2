/**
 * 기초 문서(색 · 글꼴 · 간격 · 테두리와 그림자). 값은 tokens.ts가 src/styles.css에서 읽고, 견본은
 * CSS 변수(`var(--color-…)`)로 칠해 styles.css를 저장하면 바로 바뀐다. 쓰는 곳 · 규칙은 제품 코드를 보고 적었다.
 */
import { useEffect, useState, type ReactNode } from 'react';
import {
  contrast,
  customVariants,
  lightOverrides,
  themeGroups,
  themeTokens,
  type Token,
} from './tokens';

function Page({ title, intro, children }: { title: string; intro: string; children: ReactNode }) {
  return (
    <div className="min-h-[40rem] bg-greige px-6 py-6 text-ink lg:px-10">
      <div className="mx-auto flex max-w-page flex-col gap-8">
        <header className="border-b border-line-strong pb-4">
          <h1 className="text-2xl font-bold">{title}</h1>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-stone">{intro}</p>
        </header>
        {children}
      </div>
    </div>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-bold">{title}</h2>
      {description && <p className="max-w-3xl text-sm leading-relaxed">{description}</p>}
      {children}
    </section>
  );
}

const utility = (name: string) => name.replace(/^--color-/, '');

function Swatch({ token }: { token: Token }) {
  return (
    <li className="flex min-w-0 flex-col border border-line bg-panel">
      <span
        className="h-16 border-b border-line"
        style={{ background: `var(${token.name})` }}
        aria-hidden="true"
      />
      <span className="flex flex-col gap-0.5 p-3 text-xs">
        <code className="font-mono font-bold [overflow-wrap:anywhere]">{token.name}</code>
        <span className="tabular font-mono text-stone">{token.value}</span>
        {token.note && <span>{token.note}</span>}
        <span className="text-stone">
          bg-{utility(token.name)} · text-{utility(token.name)}
        </span>
      </span>
    </li>
  );
}

/** 실제로 쓰는 글자 · 바탕 조합. [글자, 바탕, 쓰는 곳] */
const PAIRS: [string, string, string][] = [
  ['--color-ink', '--color-panel', '카드 본문'],
  ['--color-ink', '--color-greige', '앱 배경 위 본문'],
  ['--color-stone', '--color-panel', '보조 글자 · 캡션'],
  ['--color-stone', '--color-greige', '앱 배경 위 보조 글자'],
  ['--color-coral-deep', '--color-panel', '강조 글자 · 경고 문구'],
  ['--color-ink', '--color-coral-wash', '좋음 배지 · 강조 배경'],
  ['#FFFFFF', '--color-coral-deep', '주 버튼 · 나쁨 배지'],
  ['--color-panel', '--color-ink', '강함 배지'],
  ['--color-ink-stage', '--color-stage', '무대 위 본문'],
  ['--color-ink-stage', '--color-stage-panel', '무대 패널 글자'],
  ['--color-dusk-ink', '--color-dusk', '분석 대기 대화 상자'],
];

function ContrastTable({ light }: { light: boolean }) {
  const base = new Map(themeTokens().map((token) => [token.name, token.value]));
  const overrides = light ? lightOverrides() : new Map<string, string>();
  const value = (name: string) =>
    name.startsWith('#') ? name : (overrides.get(name) ?? base.get(name) ?? '#000000');
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] border-collapse text-left text-sm">
        <thead className="border-b-2 border-ink text-xs text-stone">
          <tr>
            <th className="py-2 pr-3">견본</th>
            <th className="py-2 pr-3">글자 / 바탕</th>
            <th className="py-2 pr-3">쓰는 곳</th>
            <th className="py-2 pr-3">대비</th>
            <th className="py-2">판정</th>
          </tr>
        </thead>
        <tbody>
          {PAIRS.map(([foreground, background, where]) => {
            const ratio = contrast(value(foreground), value(background));
            let verdict = '부족';
            if (ratio >= 4.5) verdict = 'AA 통과';
            else if (ratio >= 3) verdict = '큰 글자만';
            return (
              <tr key={`${foreground}/${background}`} className="border-b border-line">
                <td className="py-2 pr-3">
                  <span
                    className="inline-block px-3 py-1 font-bold"
                    style={{ color: value(foreground), background: value(background) }}
                  >
                    가나 Aa 12
                  </span>
                </td>
                <td className="py-2 pr-3 font-mono text-xs">
                  {foreground.replace('--color-', '')} / {background.replace('--color-', '')}
                </td>
                <td className="py-2 pr-3">{where}</td>
                <td className="tabular py-2 pr-3 font-mono">{ratio.toFixed(2)}</td>
                <td className={`py-2 font-bold ${ratio < 4.5 ? 'text-coral-deep' : ''}`}>
                  {verdict}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** 색. 상태: all 전체 · light 밝은 지면 · contrast 글자 대비 */
export function ColorsFoundation({ stateId }: { stateId: string }) {
  if (stateId === 'light') {
    const overrides = lightOverrides();
    const base = new Map(themeTokens().map((token) => [token.name, token.value]));
    return (
      <Page
        title="색 · 밝은 지면"
        intro="피치 생성 · 시작 전 세팅 · 안내 화면 등은 `pitch-create` 클래스 안에 놓여, 아래 색만 더 밝은 값으로 바꿔 쓴다. 이름은 같으니 컴포넌트는 그대로 두고 지면만 감싼다."
      >
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[...overrides].map(([name, value]) => (
            <li key={name} className="flex flex-col border border-line bg-panel text-xs">
              <span className="grid h-16 grid-cols-2 border-b border-line" aria-hidden="true">
                <span style={{ background: base.get(name) }} />
                <span style={{ background: value }} />
              </span>
              <span className="flex flex-col gap-0.5 p-3">
                <code className="font-mono font-bold">{name}</code>
                <span className="tabular font-mono text-stone">
                  기본 {base.get(name)} → 밝은 지면 {value}
                </span>
              </span>
            </li>
          ))}
        </ul>
        <Section title="밝은 지면의 글자 대비">
          <ContrastTable light />
        </Section>
      </Page>
    );
  }
  if (stateId === 'contrast')
    return (
      <Page
        title="색 · 글자 대비"
        intro="실제로 쓰는 글자 · 바탕 조합의 대비(WCAG 2.x). 본문은 4.5 이상, 24px 이상(굵게 19px 이상) 큰 글자는 3 이상이면 AA다. 부족한 조합은 본문에 쓰지 않는다."
      >
        <ContrastTable light={false} />
      </Page>
    );
  const groups = themeGroups().filter((group) =>
    group.tokens.some((token) => token.name.startsWith('--color-')),
  );
  return (
    <Page
      title="색"
      intro="색은 아래 이름만 쓴다. 임의값(`bg-[#EDE6DD]`)은 쓰지 않는다. Tailwind에서는 `bg-ink`처럼 `--color-` 뒤 이름으로 쓴다. 견본은 src/styles.css의 값이다."
    >
      {groups.map((group) => (
        <Section key={group.title} title={group.title} description={group.description}>
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {group.tokens
              .filter((token) => token.name.startsWith('--color-'))
              .map((token) => (
                <Swatch key={token.name} token={token} />
              ))}
          </ul>
        </Section>
      ))}
    </Page>
  );
}

// 제품 코드에서 쓰는 크기와 쓰는 곳(2026-10-29 제품 코드 기준). 많이 쓰는 순
const TYPE_SCALE: [string, string, string][] = [
  ['text-sm', '14px', '본문 · 버튼(md) · 표 — 가장 많이 쓴다'],
  ['text-xs', '12px', '캡션 · 배지 · 작은 버튼(sm) · 보조 설명'],
  ['text-lg', '18px', '카드 제목 · 안내 화면 제목'],
  ['text-3xl', '30px', '피치 생성 단계 제목 · 홈 머리 · 랜딩 · 약관 제목'],
  ['text-xl', '20px', '홈 · 피치 정보 · 대화 상자 제목'],
  ['text-2xl', '24px', '안내 화면의 큰 제목(미지원 · 재시도) · 로그인'],
  ['text-4xl', '36px', '지표 숫자(Stat)'],
  ['text-base', '16px', '피치 생성 입력칸 · 부제목'],
  ['text-5xl', '48px', '404 같은 큰 숫자'],
];

/** 글꼴. 상태: scale 크기 · 굵기 · families 글꼴 */
export function TypographyFoundation({ stateId }: { stateId: string }) {
  if (stateId === 'families') {
    const fonts = themeTokens().filter((token) => token.name.startsWith('--font-'));
    return (
      <Page
        title="글꼴"
        intro="본문은 Pretendard, 숫자 · 시간 · 영문 표시는 IBM Plex Mono(한글은 Pretendard로 넘어간다). 글꼴 파일은 설계서가 직접 가진다(public/fonts)."
      >
        {fonts.map((token) => (
          <Section key={token.name} title={token.name} description={token.value}>
            <p
              className="border border-line bg-panel p-5 text-xl"
              style={{ fontFamily: `var(${token.name})` }}
            >
              발표 연습 · TAKE 3 · 02:14 / 05:00 · 청중 응시 68%
            </p>
          </Section>
        ))}
        <Section
          title="숫자 정렬 · 줄바꿈"
          description="타이머 · 지표 · 비교표처럼 숫자가 세로로 맞아야 하는 곳은 `tabular`(고정 폭 숫자)를 붙인다. 본문은 `word-break: keep-all`이라 한국어 낱말 중간에서 줄을 바꾸지 않는다."
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <p className="border border-line bg-panel p-4 font-mono text-2xl">
              11:11
              <br />
              08:08
            </p>
            <p className="tabular border border-line bg-panel p-4 font-mono text-2xl">
              11:11
              <br />
              08:08
            </p>
          </div>
        </Section>
      </Page>
    );
  }
  return (
    <Page
      title="글꼴 · 크기와 굵기"
      intro="Tailwind 기본 크기를 쓴다. 굵기는 대부분 `font-bold` 하나로 강조하고, 보통 굵기와 섞어 위계를 만든다. 웰컴(랜딩)만 text-6xl 이상을, 웰컴 · 로그인 · 약관의 큰 제목만 font-black · extrabold를 쓴다(예외)."
    >
      <div className="flex flex-col border-t border-line">
        {TYPE_SCALE.map(([name, size, where]) => (
          <div
            key={name}
            className="grid gap-2 border-b border-line py-4 sm:grid-cols-[9rem_minmax(0,1fr)]"
          >
            <p className="font-mono text-xs">
              <span className="font-bold">{name}</span>
              <span className="block text-stone">{size}</span>
            </p>
            <div className="min-w-0">
              <p className={`${name} font-bold`}>발표 흐름을 한 번 더 점검해요</p>
              <p className="mt-1 text-xs text-stone">{where}</p>
            </div>
          </div>
        ))}
      </div>
      <Section
        title="굵기"
        description="font-bold가 기본 강조다. font-semibold · font-medium은 드물게 쓴다. font-black · extrabold는 웰컴 · 로그인 · 약관의 큰 제목에만 쓴다(예외)."
      >
        <p className="flex flex-wrap gap-6 border border-line bg-panel p-4 text-lg">
          <span className="font-normal">보통 normal</span>
          <span className="font-semibold">semibold</span>
          <span className="font-bold">굵게 bold</span>
        </p>
      </Section>
    </Page>
  );
}

const GAPS: [string, string, string][] = [
  ['gap-1', '4px', '아이콘과 글자'],
  ['gap-2', '8px', '배지 · 작은 버튼 묶음'],
  ['gap-3', '12px', '카드 안 항목 · 버튼 줄 — 가장 많이 쓴다'],
  ['gap-4', '16px', '카드 사이'],
  ['gap-6', '24px', '페이지 안 큰 덩어리'],
];

/** 간격 · 크기 */
export function SpacingFoundation() {
  const groups = themeGroups().filter((group) =>
    group.tokens.some((token) => /^--(spacing|container)-/.test(token.name)),
  );
  return (
    <Page
      title="간격 · 크기"
      intro="간격은 Tailwind 4px 단위를 쓰고, 화면 배치에 쓰는 크기만 이름을 붙여 둔다."
    >
      {groups.map((group) => (
        <Section key={group.title} title={group.title}>
          {group.description && (
            <p
              className={`max-w-3xl border-2 p-4 text-sm leading-relaxed ${group.title.includes('★') ? 'border-coral-deep bg-coral-wash' : 'border-line'}`}
            >
              {group.description}
            </p>
          )}
          <ul className="flex flex-col gap-2">
            {group.tokens
              .filter((token) => /^--(spacing|container)-/.test(token.name))
              .map((token) => (
                <li
                  key={token.name}
                  className="grid gap-1 border border-line bg-panel p-3 text-sm sm:grid-cols-[16rem_8rem_minmax(0,1fr)]"
                >
                  <code className="font-mono font-bold">{token.name}</code>
                  <span className="tabular font-mono">{token.value}</span>
                  <span className="text-stone">{token.note}</span>
                </li>
              ))}
          </ul>
        </Section>
      ))}
      <Section title="간격" description="같은 덩어리 안은 좁게, 덩어리 사이는 넓게 둔다.">
        <ul className="flex flex-col gap-2">
          {GAPS.map(([name, size, where]) => (
            <li
              key={name}
              className="grid items-center gap-3 text-sm sm:grid-cols-[6rem_4rem_10rem_minmax(0,1fr)]"
            >
              <code className="font-mono font-bold">{name}</code>
              <span className="tabular font-mono">{size}</span>
              <span className="flex h-6 items-center bg-panel">
                <span className="h-6 bg-coral" style={{ width: size }} />
              </span>
              <span className="text-stone">{where}</span>
            </li>
          ))}
        </ul>
      </Section>
      <Section
        title="누르는 영역 · 페이지 폭"
        description="누르는 것은 높이 44px 이상(`min-h-11`)으로 둔다. 작은 버튼(sm)은 36px로 표 · 카드 안에서만 쓴다. 본문 폭은 `max-w-page`(1440px), 좌우 여백은 `px-6`, 넓은 화면에서 `lg:px-10`이다."
      >
        <div className="flex flex-wrap items-end gap-3">
          <span className="flex min-h-11 items-center border-2 border-ink bg-panel px-4 text-sm font-bold">
            44px
          </span>
          <span className="flex min-h-9 items-center border-2 border-ink bg-panel px-3 text-xs font-bold">
            36px (sm)
          </span>
        </div>
      </Section>
    </Page>
  );
}

const SHADOWS: [string, string][] = [
  ['shadow-[4px_4px_0_var(--color-line)]', '카드 · 패널(Card), 시작 전 세팅 틀'],
  ['shadow-[3px_3px_0_var(--color-ink)]', '주 버튼(primary)'],
  ['shadow-[4px_4px_0_var(--color-ink)]', '홈 · 로그인의 큰 주 버튼'],
];

const BORDERS: [string, string][] = [
  ['border border-line', '구분선 · 목록 칸 — 가장 많이 쓴다'],
  ['border-2 border-ink', '버튼 · 강조 틀 · 선택된 항목'],
  ['border border-dashed border-line-strong', '빈 자리 · 선택 항목(안 해도 되는 것)'],
];

/** 테두리 · 그림자 · 모서리 · 포커스 */
export function SurfacesFoundation() {
  return (
    <Page
      title="테두리 · 그림자"
      intro="흐린 그림자 대신 어긋난 단색 그림자와 선으로 면을 나눈다. 모서리는 기본으로 각지게 둔다."
    >
      <Section title="테두리">
        <ul className="grid gap-4 sm:grid-cols-3">
          {BORDERS.map(([name, where]) => (
            <li key={name} className="flex flex-col gap-2">
              <span className={`h-20 bg-panel ${name}`} aria-hidden="true" />
              <code className="font-mono text-xs font-bold">{name}</code>
              <span className="text-xs text-stone">{where}</span>
            </li>
          ))}
        </ul>
      </Section>
      <Section title="그림자" description="흐린 그림자(`shadow` · `shadow-sm`)는 거의 쓰지 않는다.">
        <ul className="grid gap-6 sm:grid-cols-3">
          {SHADOWS.map(([name, where]) => (
            <li key={name} className="flex flex-col gap-2">
              <span className={`h-20 border-2 border-ink bg-panel ${name}`} aria-hidden="true" />
              <code className="font-mono text-xs font-bold [overflow-wrap:anywhere]">{name}</code>
              <span className="text-xs text-stone">{where}</span>
            </li>
          ))}
        </ul>
      </Section>
      <Section
        title="모서리"
        description="면은 각지게 둔다. 피치 생성 화면은 `pitch-create-square`로 안쪽 rounded를 모두 0으로 바꾼다. 둥근 것은 단계 번호 · 음량 막대 · 카메라 켜기 같은 원형 표시(`rounded-full`)다. 예외: 웰컴 카운트다운 숫자판(FlipNumber, rounded-md · sm)은 일부러 둥글다. 제품에 남은 예외(FE 확인): 로그인 버튼 감싸개(rounded) · 시선 기준 화면(GazeSetupView, rounded-lg) · 음량 막대(LevelBar, rounded-xs)."
      >
        <div className="flex flex-wrap items-center gap-4">
          <span className="flex h-14 w-24 items-center justify-center border-2 border-ink bg-panel text-xs">
            각진 면
          </span>
          <span className="flex size-10 items-center justify-center rounded-full bg-ink text-sm font-bold text-panel">
            1
          </span>
          <span className="rounded-full bg-coral-deep px-4 py-2 text-xs font-semibold text-white">
            카메라·마이크 켜기
          </span>
        </div>
      </Section>
      <Section
        title="포커스"
        description="키보드 조작이 요구사항이라 포커스 링을 지우지 않는다. 기본은 코랄 2px 선, 2px 띄움(`:focus-visible`). 예외: 밝은 지면의 입력칸(피치 생성의 대본 · 평가기준 · 정보, 리뷰 창, 의견 종류)은 ink(`focus-visible:outline-ink`), 대본 확인은 dusk 색이다. 제품에 남은 예외(FE 확인): 슬라이드 칸 한 곳이 outline-none이다."
      >
        <span className="inline-flex min-h-11 items-center border-2 border-ink bg-panel px-6 text-sm font-bold outline outline-2 outline-offset-2 outline-coral">
          포커스된 버튼
        </span>
      </Section>
    </Page>
  );
}

/** Tailwind 기본 크기 기준(min-width). 제품 · 설계서가 따로 바꾸지 않았다 */
const BREAKPOINTS: [string, string, string][] = [
  ['sm', '40rem (640px)', '버튼 · 메뉴를 한 줄로, 글자 크기 한 단계, 카드 2열 시작'],
  ['md', '48rem (768px)', '카드 2~3열(분석 방식 설명 · 사용방법 · FAQ · 연구실 구성원)'],
  [
    'lg',
    '64rem (1024px)',
    '두 단 배치(피치 생성 사이드바 · 시작 전 세팅 오른쪽 결정 칸 · 웰컴 첫 화면), 좌우 여백 px-10, 피치 생성 높이 고정',
  ],
  ['xl', '80rem (1280px)', '시작 전 세팅의 장치 선택 · 점검 항목을 나란히, 웰컴 큰 제목 7xl'],
  ['2xl', '96rem (1536px)', '거의 쓰지 않는다'],
];

// stage.css의 미디어 쿼리. 무대는 Tailwind 기준 대신 자기 크기 기준을 쓴다
const STAGE_QUERIES: [string, string][] = [
  [
    '(max-width: 900px)',
    '레일을 아래로 접어 슬라이드를 살린다. 카메라 · 범례는 숨긴다. 대본 높이는 그대로다.',
  ],
  ['(max-height: 800px)', '다음 장 썸네일만 접는다. 카메라는 항상 남는다.'],
  ['(max-width: 600px)', '여백 · 레일 폭 · 코치 줄을 줄인다.'],
];

const TAILWIND_QUERIES: [string, string][] = [
  ['sm', '(min-width: 40rem)'],
  ['md', '(min-width: 48rem)'],
  ['lg', '(min-width: 64rem)'],
  ['xl', '(min-width: 80rem)'],
  ['2xl', '(min-width: 96rem)'],
];

/** 지금 이 미리보기(iframe) 크기와 켜진 기준. 미리보기 크기를 바꾸면 따라 바뀐다 */
function LiveSize() {
  const read = () => ({ width: window.innerWidth, height: window.innerHeight });
  const [size, setSize] = useState(read);
  useEffect(() => {
    const update = () => setSize(read());
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);
  const queries = [
    ...TAILWIND_QUERIES,
    ...customVariants().map(({ name, query }): [string, string] => [name, query]),
  ];
  const on = (query: string) =>
    typeof window.matchMedia === 'function' && window.matchMedia(query).matches;
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-3 border-2 border-ink bg-panel p-4"
    >
      <p className="tabular font-mono text-lg font-bold">
        {size.width} × {size.height}
      </p>
      <ul className="flex flex-wrap gap-2">
        {queries.map(([name, query]) => (
          <li
            key={name}
            className={`px-2 py-0.5 font-mono text-xs font-bold ${on(query) ? 'bg-ink text-panel' : 'border border-dashed border-line-strong text-stone'}`}
          >
            {on(query) ? '✓' : '○'} {name}
          </li>
        ))}
      </ul>
      <p className="text-xs text-stone">위의 "미리보기 크기"를 바꾸면 따라 바뀐다.</p>
    </div>
  );
}

/** 첫 칸은 이름(굵게), 마지막 칸은 예 · 쓰는 곳(흐리게) */
function cellClass(index: number, length: number) {
  if (index === 0) return 'font-bold';
  return index === length - 1 ? 'text-stone' : '';
}

function Rows({ rows, columns }: { rows: string[][]; columns: string }) {
  return (
    <ul className="flex flex-col gap-2">
      {rows.map((row) => (
        <li
          key={row[0]}
          className={`grid gap-1 border border-line bg-panel p-3 text-sm ${columns}`}
        >
          {row.map((cell, index) => (
            <span key={index} className={cellClass(index, row.length)}>
              {cell}
            </span>
          ))}
        </li>
      ))}
    </ul>
  );
}

/** 반응형 기준 */
export function ResponsiveFoundation() {
  return (
    <Page
      title="반응형 기준"
      intro="작은 화면부터 쓰고 넓어질 때 바꾼다(min-width). 기준은 Tailwind 기본값, 설계서가 더한 one-screen, 무대 전용 stage.css 기준이다. 미리보기는 iframe이라 이 문서도 그 크기에 반응한다."
    >
      <LiveSize />
      <Section
        title="Tailwind 기준"
        description="sm: · md: 같은 앞붙이가 이 폭부터 켜진다. 자주 쓰는 것은 sm · lg다."
      >
        <Rows rows={BREAKPOINTS} columns="sm:grid-cols-[4rem_10rem_minmax(0,1fr)]" />
      </Section>
      <Section
        title="설계서가 더한 기준"
        description="styles.css의 @custom-variant에서 읽는다. 홈을 한 화면에 고정할 만큼 넓고 높은 창에서만 켠다 — 배율 125~150% 노트북처럼 낮으면 고정하지 않고 스크롤한다."
      >
        <Rows
          rows={customVariants().map(({ name, query }) => [`${name}:`, query])}
          columns="sm:grid-cols-[8rem_minmax(0,1fr)]"
        />
      </Section>
      <Section
        title="무대(stage.css) 기준"
        description="리허설 무대는 자기 크기 기준을 둔다(stage.css 주석: 준비하며 태블릿으로 열어 보는 경우). 대본 칸 높이(계측 조건)는 어느 크기에서도 바꾸지 않는다."
      >
        <Rows rows={STAGE_QUERIES} columns="sm:grid-cols-[12rem_minmax(0,1fr)]" />
      </Section>
      <Section
        title="확인할 크기"
        description="제품은 노트북 · 데스크톱 전용이고 팀 기준 크기는 1366 × 650 · 1920 × 950이다. 이 두 크기와 브라우저 확대 125% · 150%(배율을 올린 노트북)에서 가로 넘침 · 잘림이 없어야 한다. 휴대폰 · 태블릿 크기는 참고용이다(#710에서 모바일 접속 차단 예정). 미리보기 크기 맨 위의 팀 기준을 고르고 브라우저 확대를 바꿔 본다."
      />
    </Page>
  );
}

// 말투 · 상황별 문구 · 표기. 제품 문구(2026-10-29)를 보고 적었다
const TONE: string[][] = [
  ['기본', '해요체', '"연습 기록을 불러오지 못했어요." — 제품 문구 대부분'],
  ['치치(연습생)', '반말 · 함께 하는 말투', '"어라, 여기는 아무것도 없어. 길을 잃었나 봐!"'],
  ['피코(교수)', '존댓말 · 짚어 주는 말투', '"괜찮습니다. 처음부터 잘하는 새는 없어요."'],
];

const PATTERNS: string[][] = [
  ['실패', '무엇이 안 됐는지 + 할 일', '"동의 설정을 바꾸지 못했어요. 다시 시도해 주세요."'],
  [
    '진행 중',
    '"…하는 중이에요", 버튼은 "…하는 중…"',
    '"말하기 분석 서버에 연결하는 중이에요" · "끝내는 중…"',
  ],
  ['막힌 이유', '지금 막는 것 하나만, 할 일로', '"마이크에 대고 한 마디 해보세요"'],
  ['버튼', '하는 일을 동사로, 이동은 "…로"', '"Take 3 시작하기" · "다시 불러오기" · "내 피치로"'],
  [
    '되돌리기 어려움',
    '결과를 먼저 말하고 묻는다',
    '"탈퇴하면 피치 · 연습 기록 · 리포트가 모두 지워지고 되돌릴 수 없어요. 탈퇴할까요?"',
  ],
  ['빈 자리', '왜 비었는지 + 다음 할 일', '"아직 연습한 Take가 없어요"'],
];

const FORMATS: string[][] = [
  ['측정 못 한 값', '"—"와 이유를 쓴다. 0으로 채우지 않는다'],
  ['시간', 'mm:ss(05:12). 남은 시간은 "남은 02:46"'],
  ['비율 · 변화', '68%, "지난 Take보다 +6%p"'],
  ['목록 날짜', 'MM.DD(10.28)'],
  [
    '용어',
    '피치(발표 하나) · Take(한 번의 연습, T는 늘 대문자) · 슬라이드 · 대본 · 시작 전 세팅 · 리허설 · 리포트',
  ],
  ['화살표', '"→" 다음 단계, "←" 이전 · 뒤로, "↗" 새 흐름 · 바깥으로'],
  ['나열', '항목 사이는 " · "(가운데점, 양옆 띄움)'],
];

/** 문구 규칙. 상태: tone 말투 · patterns 상황별 문구 · format 표기 */
export function WritingFoundation({ stateId }: { stateId: string }) {
  if (stateId === 'patterns')
    return (
      <Page title="문구 · 상황별" intro="사용자가 지금 할 수 있는 일을 한 문장으로 알린다.">
        <Rows rows={PATTERNS} columns="sm:grid-cols-[8rem_14rem_minmax(0,1fr)]" />
      </Page>
    );
  if (stateId === 'format')
    return (
      <Page title="문구 · 표기" intro="숫자 · 용어 · 기호는 화면마다 같게 쓴다.">
        <Rows rows={FORMATS} columns="sm:grid-cols-[10rem_minmax(0,1fr)]" />
      </Page>
    );
  return (
    <Page
      title="문구 · 말투"
      intro="기본은 해요체다. 마스코트는 각자의 말투가 있다. 새 문구는 해요체로 쓴다."
    >
      <Rows rows={TONE} columns="sm:grid-cols-[8rem_12rem_minmax(0,1fr)]" />
      <Section
        title="알려진 문제: 합니다체가 섞인 곳"
        description="로그인 상태 줄 · 약관 · 웰컴 일부 · 시작 전 세팅 안내 줄(…할 수 있습니다) · 대본 표시 설명(…붙습니다) 등 일부는 합니다체다. 한 화면 안에서 섞이면 어색하니 해요체로 맞출지 FE와 정한다. 설계서 샘플은 제품 문구를 그대로 둔다."
      />
    </Page>
  );
}
