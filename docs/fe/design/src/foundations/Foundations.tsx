/**
 * 기초 문서(색 · 글꼴 · 간격 · 테두리와 그림자). 값은 tokens.ts가 src/styles.css에서 읽고, 견본은
 * CSS 변수(`var(--color-…)`)로 칠해 styles.css를 저장하면 바로 바뀐다. 쓰는 곳 · 규칙은 제품 코드를 보고 적었다.
 */
import type { ReactNode } from 'react';
import { contrast, lightOverrides, themeGroups, themeTokens, type Token } from './tokens';

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
  children: ReactNode;
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
  ['text-xl', '20px', '홈 · 피치 정보 · 대화 상자 제목'],
  ['text-2xl', '24px', '안내 화면의 큰 제목(미지원 · 재시도) · 로그인'],
  ['text-3xl', '30px', '피치 생성 단계 제목 · 홈 머리'],
  ['text-4xl', '36px', '지표 숫자(Stat)'],
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
      intro="Tailwind 기본 크기를 쓴다. 굵기는 대부분 `font-bold` 하나로 강조하고, 보통 굵기와 섞어 위계를 만든다."
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
        description="font-bold가 기본 강조다. font-semibold · font-medium은 드물게, font-black은 쓰지 않는다."
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
        description="면은 각지게 둔다. 피치 생성 화면은 `pitch-create-square`로 안쪽 rounded를 모두 0으로 바꾼다. 둥근 것은 단계 번호 · 음량 막대 · 카메라 켜기 같은 원형 표시(`rounded-full`)뿐이다."
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
        description="키보드 조작이 요구사항이라 포커스 링을 지우지 않는다. 모든 요소에 코랄 2px 선, 2px 띄움(`:focus-visible`)."
      >
        <span className="inline-flex min-h-11 items-center border-2 border-ink bg-panel px-6 text-sm font-bold outline outline-2 outline-offset-2 outline-coral">
          포커스된 버튼
        </span>
      </Section>
    </Page>
  );
}
