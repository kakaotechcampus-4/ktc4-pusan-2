/**
 * 디자인 값 읽기. 원본은 src/styles.css 하나이고, 기초 문서는 그 글을 그대로 읽어 보여 준다 —
 * 문서에 값을 따로 적으면 styles.css를 고칠 때 문서가 낡는다. 저장하면 개발 서버가 이 모듈을 다시 읽는다.
 */
import css from '../styles.css?raw';

export interface Token {
  /** `--color-ink` 같은 CSS 변수 이름 */
  name: string;
  value: string;
  /** 같은 줄 끝 주석 */
  note?: string;
  /** 위쪽 `── 제목 ──` 주석의 제목. 없으면 '기타' */
  group: string;
}

export interface TokenGroup {
  title: string;
  /** 제목 주석 안의 나머지 설명(쓰는 곳 · 규칙) */
  description: string;
  tokens: Token[];
}

/**
 * `{ … }` 한 덩어리의 본문. 중첩이 없는 블록(@theme · 클래스)만 쓴다.
 * 끝은 줄 맨 앞의 `}`로 찾으므로, 블록 안에 들여쓴 `}`나 중첩 블록을 두면 값이 잘린다(tokens.test가 잡는다)
 */
function block(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) return '';
  const open = css.indexOf('{', start);
  return css.slice(open + 1, css.indexOf('\n}', open));
}

const declaration = /(--[\w-]+)\s*:\s*([^;]+);[ \t]*(?:\/\*\s*(.*?)\s*\*\/)?/g;
const heading = /\/\*\s*──\s*(.+?)\s*─+([\s\S]*?)\*\//g;

function clean(value: string) {
  return value.replace(/\s+/g, ' ').trim();
}

/** @theme의 값을 제목 주석별로 묶는다 */
export function themeGroups(): TokenGroup[] {
  const text = block('@theme');
  const marks = [...text.matchAll(heading)].map((match) => ({
    at: match.index,
    title: match[1]!.trim(),
    description: clean(match[2]!.replace(/^\s*\*?/gm, '')),
  }));
  const groups = new Map<string, TokenGroup>();
  for (const match of text.matchAll(declaration)) {
    const mark = marks.filter((item) => item.at < match.index).at(-1);
    const title = mark?.title ?? '기타';
    if (!groups.has(title))
      groups.set(title, { title, description: mark?.description ?? '', tokens: [] });
    groups.get(title)!.tokens.push({
      name: match[1]!,
      value: clean(match[2]!),
      note: match[3],
      group: title,
    });
  }
  return [...groups.values()];
}

export function themeTokens(): Token[] {
  return themeGroups().flatMap((group) => group.tokens);
}

/** 밝은 지면(`.pitch-create`)이 바꾸는 값. 이름 → 값 */
export function lightOverrides(): Map<string, string> {
  return new Map(
    [...block('.pitch-create').matchAll(declaration)].map((match) => [match[1]!, clean(match[2]!)]),
  );
}

/** `#RRGGBB`의 상대 휘도(WCAG 2.x) */
function luminance(hex: string) {
  const channels = hex
    .replace('#', '')
    .match(/../g)!
    .map((part) => {
      const value = parseInt(part, 16) / 255;
      return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    });
  return 0.2126 * channels[0]! + 0.7152 * channels[1]! + 0.0722 * channels[2]!;
}

/** 두 색의 대비(1~21). WCAG AA 기준은 본문 4.5, 큰 글자(24px 또는 19px 굵게) 3 */
export function contrast(foreground: string, background: string) {
  const [a, b] = [luminance(foreground), luminance(background)].sort((x, y) => y - x);
  return (a! + 0.05) / (b! + 0.05);
}

/** styles.css의 `@custom-variant 이름 (@media …);` — Tailwind 기본 크기 말고 설계서가 더한 기준 */
export function customVariants(): { name: string; query: string }[] {
  return [...css.matchAll(/@custom-variant\s+([\w-]+)\s+\(@media\s+(.+?)\);/g)].map((match) => ({
    name: match[1]!,
    query: match[2]!.trim(),
  }));
}
