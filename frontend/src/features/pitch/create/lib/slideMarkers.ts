/**
 * 대본 속 슬라이드 구분자 찾기 — AI `script_parser-1.3` 과 같은 규칙입니다.
 *
 * AI script-parser(`features/script_parser/splitter.py` `match_delimiter`)가 대본을 나누는 규칙을
 * FE 에서 미리 따라 해 보는 것입니다. 대본 입력 화면은 이걸로 "구분 3 / 8" 을 보여 주고,
 * 목(`mocks/scripts.ts`)은 이걸로 나눕니다 — 세 곳이 같은 규칙을 써야 화면에 보인 개수와
 * 서버에 저장되는 장수가 어긋나지 않습니다. 규칙 표는 `docs/ai/features/script-parser.md` 5-2 입니다.
 *
 * 구분자는 줄 맨 앞(앞 공백 · 탭 허용, 그 뒤 마크다운 제목 표시 `#`~`######` + 공백 허용)의
 *   ① 대괄호:  `[1]` · `[슬라이드 1]` · `[Slide 1]`            → 뒤 같은 줄 글은 본문
 *   ② 콜론:    `슬라이드 1:` · `Slide 1 :` · `슬라이드 1：`    → 뒤 같은 줄 글은 본문
 *   ③ 줄 전체: `슬라이드 1` · `Slide1` (뒤에 글이 없음)
 *   ④ 제목 표시 뒤: `## Slide 1 — 제목` · `# 슬라이드 2 개요` → 구분 기호(— – - | : .)를 걷은 뒤 글은 본문
 * 입니다. `슬라이드`와 번호 사이 공백은 없어도 되고, `Slide` 는 대소문자를 가리지 않습니다. 번호는 1~4자리.
 * 구분자가 아닌 것:
 *   - 제목 표시 없이 뒤에 글이 붙은 `슬라이드 3 보시면 …` (문장일 수 있습니다)
 *   - 번호 목록 `1.` · `1)` (본문 나열과 헷갈립니다)
 *   - 대괄호 바로 뒤가 `(` 인 마크다운 링크 `[3](https://…)`
 *   - 제목 표시 뒤 구분 기호 바로 다음이 숫자인 범위 · 소수 `## Slide 1-2` · `## Slide 3.14`
 *   - `**[슬라이드 1]**` · `> [1]` 처럼 `#` 말고 다른 꾸밈, `【슬라이드 1】` 같은 다른 괄호
 *
 * ★ 이 정규식은 AI 와 **같은 결과**를 내야 합니다. 공통 입력 목록
 *   `docs/ai/features/script-parser-markers.json` 을 FE(`slideMarkers.test.ts`)와
 *   AI(`test_splitter.py`)가 함께 읽습니다. 규칙을 바꾸면 그 목록과 AI 를 같이 고칩니다 —
 *   FE 가 AI 보다 넓게 인식하면 화면에서는 나뉘는데 서버에는 1장으로 저장됩니다.
 *   구분처럼 보이지만 인식되지 않는 줄은 개수에 넣지 않고 첫 구분 앞에서만 찾아 안내합니다(`MarkerScan.near`).
 * ★ Python 과 JS 가 다른 곳을 맞췄습니다.
 *   - 숫자 `\d`: Python 은 전각 숫자(１２３)도 숫자로 봅니다. 검사할 때만 전각 숫자(０~９)를 ASCII 로
 *     바꿔 맞춥니다(`toAsciiDigits`). 사용자 대본 원문은 바꾸지 않습니다. 그 밖의 유니코드 숫자
 *     (아라비아 · 데바나가리 등)는 AI 만 숫자로 봅니다 — 한글 입력기로는 칠 수 없어 맞추지 않았습니다.
 *   - 대소문자: Python `re.IGNORECASE` 는 `slide` 의 s · i 에 `ſ` · `ı` · `İ` 도 맞춥니다. JS `i` 는
 *     그러지 않아 글자 묶음으로 적었습니다(`SLIDE`).
 *   - 공백 걷기: Python `str.strip()` 과 JS `trim()` 이 걷는 글자가 다릅니다(BOM · `\x1f`). `pyStrip` 을 씁니다.
 *   - 줄 나누기: Python `str.splitlines()` 와 같게 나눕니다(`splitLines`).
 */

const SLIDE = '(?:슬라이드|[sSſ][lL][iIıİ][dD][eE])[ \\t]*';
const HEADING = /^#{1,6}[ \t]+/;
const BRACKET = new RegExp(`^\\[(?:${SLIDE})?(\\d{1,4})\\](?!\\()`);
const WORD = new RegExp(`^${SLIDE}(\\d{1,4})(?!\\d)`);
const COLON = /^[ \t]*[:：]/;
// 제목 표시 뒤 번호와 제목 사이는 공백이나 구분 기호로 띄웁니다 ("## Slide 1a" 는 아닙니다)
const HEADING_SEP = /^(?:[ \t]*[—–\-|:：.]?[ \t]+|[ \t]*[—–\-|:：.])/;
// 구분 기호 바로 다음 숫자: 범위(1-2) · 소수(3.14)
const HEADING_RANGE = /^[ \t]*[—–\-|.]\d/;

/** Python `str.isspace()` 인 글자 — `str.strip()` 이 걷는 것 */
const PY_SPACE =
  '\\t\\n\\v\\f\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';
const PY_STRIP = new RegExp(`^[${PY_SPACE}]+|[${PY_SPACE}]+$`, 'g');

/** Python `str.strip()` 과 같은 앞뒤 공백 걷기 */
function pyStrip(text: string): string {
  return text.replace(PY_STRIP, '');
}

/** 전각 숫자(０~９)를 ASCII 숫자로. 한 글자가 한 글자로 바뀌어 길이와 위치가 그대로입니다 */
export function toAsciiDigits(text: string): string {
  return text.replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));
}

/** Python `str.splitlines()` 와 같은 줄바꿈 (CRLF · VT · FF · FS/GS/RS · NEL · LS · PS) */
export function splitLines(text: string): string[] {
  // oxlint-disable-next-line no-control-regex -- 제어문자 줄바꿈이 의도된 규칙입니다
  return text.split(/\r\n|[\n\r\v\f\x1c-\x1e\u0085\u2028\u2029]/);
}

export interface MarkerMatch {
  /** 장 번호. 0 이면 구분자 줄이지만 대본 전체가 나뉘지 않습니다 */
  number: number;
  /** 구분자 뒤 같은 줄의 글(앞뒤 공백 제외) — 그 장의 본문입니다. 원문(전각 숫자 포함)에서 자릅니다 */
  rest: string;
}

/** AI `match_delimiter` 와 같습니다 — 구분자 줄이면 번호와 뒤 글, 아니면 null */
export function matchMarker(line: string): MarkerMatch | null {
  const ascii = toAsciiDigits(line);
  // 위치만 ASCII 쪽에서 재고 글은 원문에서 자릅니다 — 두 글은 길이가 같습니다
  let at = ascii.length - ascii.replace(/^[ \t]+/, '').length;
  const heading = HEADING.exec(ascii.slice(at));
  if (heading) at += heading[0].length;

  const bracket = BRACKET.exec(ascii.slice(at));
  if (bracket)
    return { number: Number(bracket[1]), rest: pyStrip(line.slice(at + bracket[0].length)) };

  const word = WORD.exec(ascii.slice(at));
  if (!word) return null;
  const number = Number(word[1]);
  at += word[0].length;
  const after = ascii.slice(at);

  const colon = COLON.exec(after);
  if (colon) return { number, rest: pyStrip(line.slice(at + colon[0].length)) };
  if (pyStrip(after) === '') return { number, rest: '' };
  if (heading && !HEADING_RANGE.test(after)) {
    const sep = HEADING_SEP.exec(after);
    if (sep) return { number, rest: pyStrip(line.slice(at + sep[0].length)) };
  }
  return null;
}

/** 이 줄이 구분자면 그 번호, 아니면 null */
export function markerNumber(line: string): number | null {
  return matchMarker(line)?.number ?? null;
}

/** 구분자 뒤 같은 줄의 글(앞뒤 공백 제외) — 구분자가 아니면 줄 그대로 */
export function stripMarker(line: string): string {
  return matchMarker(line)?.rest ?? line;
}

/**
 * 구분처럼 보이지만 인식되지 않는 줄 — 개수에는 넣지 않고 안내에만 씁니다.
 * - `space`: 줄 맨 앞에 전각 공백 · NBSP · BOM 같은 특수 공백이 있어 인식되지 않음 (`\u3000[1]`)
 * - `form`: 꾸밈이나 다른 괄호를 걷으면 구분자 (`**[슬라이드 1]**` · `> [1]` · `【슬라이드 1】` · `#Slide 1` · `[ 1 ]`)
 * - `named`: `슬라이드 1.` · `슬라이드 1 - 도입` 처럼 장을 가리켰지만 구분자 꼴이 아님
 * - `numbered`: `1.` · `1)` 꼴. 본문 번호 목록과 생김새가 같아 바꾸라고 하지 않을 때가 많습니다
 */
export type NearMarkerKind = 'space' | 'form' | 'named' | 'numbered';

const LEADING_SPACE = /^\s+/;
/** 줄 앞의 마크다운 꾸밈(`#` · `*` · `_` · `>` · `-` · `+` · `•`)과 공백 */
const DECORATION = /^(?:[#*_>+•-]+\s*)+/;
const TRAILING_DECORATION = /[*_\s]+$/;
const OPEN_BRACKETS = /[【〔［〖「『]/g;
const CLOSE_BRACKETS = /[】〕］〗」』]/g;
const NAMED = /^[ \t]*(?:slide|슬라이드)[ \t]*\d{1,4}(?=[\s:.\-–)]|$)/i;
const NUMBERED = /^[ \t]*\d{1,2}[ \t]*[.)](?=[ \t]|$)/;

export function nearMarkerKind(line: string): NearMarkerKind | null {
  if (markerNumber(line) !== null || line.trim() === '') return null;
  const body = line.replace(LEADING_SPACE, '');
  if (body !== line.replace(/^[ \t]+/, '') && markerNumber(body) !== null) return 'space';
  const loose = body
    .replace(DECORATION, '')
    .replace(TRAILING_DECORATION, '')
    .replace(OPEN_BRACKETS, '[')
    .replace(CLOSE_BRACKETS, ']')
    .replace(/\[\s+/g, '[')
    .replace(/\s+\]/g, ']')
    .replace(/\s/g, ' ');
  if (markerNumber(loose) !== null) return 'form';
  const ascii = toAsciiDigits(line);
  if (NAMED.test(ascii)) return 'named';
  if (NUMBERED.test(ascii)) return 'numbered';
  return null;
}

export interface NearMarker {
  /** 줄의 글 (앞뒤 공백 제외) */
  line: string;
  kind: NearMarkerKind;
}

export interface MarkerScan {
  /** 찾은 구분자의 번호, 나온 순서대로 */
  numbers: number[];
  /**
   * 번호가 AI 가 받는 순서인가 — 1 이상이고 앞 장보다 커야 합니다 (0 · 겹침 · 거꾸로면 AI 는
   * 나누지 못한 결과로 두고 대본 전체를 한 장으로 연결합니다).
   */
  numbersValid: boolean;
  /**
   * 첫 구분자 앞에 제목이 아닌 글이 있어 보이나 — **어림입니다**. AI 는 첫 구분 앞(머리글)에서
   * LLM 이 고른 제목 줄을 빼고도 글이 남으면 대본 전체를 나누지 않습니다
   * (`script_parser/core.py` 4단계). 제목 판정은 LLM 이 해서 FE 가 미리 알 수 없으므로,
   * 빈 줄이 아닌 줄이 **두 줄 이상**이면 알립니다. 그래서 제목 + 부제 두 줄은 AI 가 나누는데 FE 는
   * 알리고, 인사말 한 줄은 AI 가 나누지 않는데 FE 는 넘어갑니다.
   */
  leadingText: boolean;
  /**
   * 구분처럼 보이지만 인식되지 않는 줄 (`nearMarkerKind`). **첫 구분자 앞의 줄만** 셉니다 —
   * 구분자 뒤의 "1. 첫째 이유는" · "Slide 3 shows" · "> [1] 인용" 은 본문입니다.
   */
  near: NearMarker[];
}

export function scanSlideMarkers(text: string): MarkerScan {
  const numbers: number[] = [];
  const near: NearMarker[] = [];
  let leadingLines = 0;
  for (const line of splitLines(text)) {
    const n = markerNumber(line);
    if (n !== null) numbers.push(n);
    else if (numbers.length === 0) {
      const kind = nearMarkerKind(line);
      if (kind) near.push({ line: line.trim(), kind });
      if (line.trim() !== '') leadingLines++;
    }
  }
  const numbersValid = numbers.every((n, i) => n >= 1 && (i === 0 || n > numbers[i - 1]));
  return { numbers, numbersValid, leadingText: leadingLines > 1, near };
}
