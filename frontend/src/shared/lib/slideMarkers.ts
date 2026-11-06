/**
 * 대본 속 슬라이드 구분자 찾기 — `[N]` · `[슬라이드 N]` · `슬라이드 N:` 세 형식만 인식합니다.
 *
 * AI script-parser(`features/script_parser/splitter.py` `DELIMITER_RE`)가 대본을 나누는 규칙을
 * FE 에서 미리 따라 해 보는 것입니다. 대본 입력 화면은 이걸로 "구분 3 / 8" 을 보여 주고,
 * 목(`mocks/scripts.ts`)은 이걸로 나눕니다 — 세 곳이 같은 규칙을 써야 화면에 보인 개수와
 * 서버에 저장되는 장수가 어긋나지 않습니다.
 *
 * ★ 이 정규식은 AI 와 **글자 그대로 같은 정의**여야 합니다 (줄 맨 앞 · 번호 1~4자리 · 콜론 필수 등).
 *   AI 가 형식을 넓히면 이 파일과 테스트(`slideMarkers.test.ts` 의 공통 목록)를 함께 고칩니다.
 *   예전에 FE 가 인식하던 넓은 표기(`Slide 1` · `1.` · `슬라이드 1` 등)는 구분으로 세지 않고,
 *   첫 구분자 앞에서만 `isLegacyMarker` 로 찾아 "구분으로 인식되지 않아요" 라고 안내합니다.
 *   구분자 뒤 본문의 "1. 첫째 이유는" 같은 번호 목록은 예전 표기가 아니라 본문입니다.
 *   이 결과는 **안내용**이고, 이걸로 매핑을 막지는 않습니다.
 *   "첫째", "다음으로" 같은 전환 표현과 빈 줄은 AI 도 구분자로 보지 않습니다.
 * ★ 숫자 `\d` 의 뜻이 다릅니다 — Python 은 전각 숫자(１２３)도 숫자로 봐서 AI 는 `[１]` 을 구분자로
 *   나누는데 JS 는 ASCII 만 봅니다. 그래서 검사할 때만 전각 숫자(０~９)를 ASCII 로 바꿔 AI 와 맞춥니다
 *   (`toAsciiDigits`). 사용자 대본 원문은 바꾸지 않습니다. 그 밖의 유니코드 숫자(아라비아 · 데바나가리 등)는
 *   AI 만 숫자로 봅니다 — 한글 입력기로는 칠 수 없어 맞추지 않았습니다.
 */

export const SLIDE_MARKER =
  /^[ \t]*(?:\[(\d{1,4})\]|\[슬라이드[ \t]+(\d{1,4})\]|슬라이드[ \t]+(\d{1,4}):)/;

/** 전각 숫자(０~９)를 ASCII 숫자로. 한 글자가 한 글자로 바뀌어 길이와 위치가 그대로입니다 */
export function toAsciiDigits(text: string): string {
  return text.replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));
}

/**
 * 예전 표기 — 구분으로는 세지 않고 안내에만 씁니다. `Slide 1` · `슬라이드 1` 처럼 장을 가리킨 것과
 * `1.` · `1)` 꼴(본문 번호 목록과 생김새가 같습니다)을 나눠 둡니다
 */
const LEGACY_NAMED = /^[ \t]*(?:slide|슬라이드)[ \t]*\d{1,4}(?=[\s:.\-–)]|$)/i;
const LEGACY_NUMBERED = /^[ \t]*\d{1,2}[ \t]*[.)](?=[ \t]|$)/;

/** Python `str.splitlines()` 와 같은 줄바꿈 (CRLF · VT · FF · FS/GS/RS · NEL · LS · PS) */
function splitLines(text: string): string[] {
  // oxlint-disable-next-line no-control-regex -- 제어문자 줄바꿈이 의도된 규칙입니다
  return text.split(/\r\n|[\n\r\v\f\x1c-\x1e\u0085\u2028\u2029]/);
}

/** 이 줄이 구분자로 시작하면 그 번호, 아니면 null */
export function markerNumber(line: string): number | null {
  const m = SLIDE_MARKER.exec(toAsciiDigits(line));
  return m ? Number(m[1] ?? m[2] ?? m[3]) : null;
}

/** 구분자 뒤 같은 줄의 글 — 구분자가 아니면 줄 그대로. 원문(전각 숫자 포함)에서 자릅니다 */
export function stripMarker(line: string): string {
  const m = SLIDE_MARKER.exec(toAsciiDigits(line));
  return m ? line.slice(m[0].length) : line;
}

/** 구분자는 아니지만 예전에 구분으로 쓰던 표기(`Slide 1` · `1.` 등)로 보이는 줄 */
export function isLegacyMarker(line: string): boolean {
  const ascii = toAsciiDigits(line);
  return markerNumber(line) === null && (LEGACY_NAMED.test(ascii) || LEGACY_NUMBERED.test(ascii));
}

/** 예전 표기 중 `Slide 1` · `슬라이드 1` 처럼 장을 가리킨 게 분명한 줄 (`1.` · `1)` 꼴은 아님) */
export function isNamedLegacyMarker(line: string): boolean {
  return markerNumber(line) === null && LEGACY_NAMED.test(toAsciiDigits(line));
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
   * 구분으로는 안 세지만 예전 표기처럼 보이는 줄의 글 (앞뒤 공백 제외). **첫 구분자 앞의 줄만**
   * 셉니다 — 구분자 뒤의 "1. 첫째 이유는" · "Slide 3 shows" 는 본문의 번호 목록 · 문장입니다.
   */
  legacy: string[];
}

export function scanSlideMarkers(text: string): MarkerScan {
  const numbers: number[] = [];
  const legacy: string[] = [];
  let leadingLines = 0;
  for (const line of splitLines(text)) {
    const n = markerNumber(line);
    if (n !== null) numbers.push(n);
    else if (numbers.length === 0) {
      if (isLegacyMarker(line)) legacy.push(line.trim());
      if (line.trim() !== '') leadingLines++;
    }
  }
  const numbersValid = numbers.every((n, i) => n >= 1 && (i === 0 || n > numbers[i - 1]));
  return { numbers, numbersValid, leadingText: leadingLines > 1, legacy };
}

/** 줄 단위로 나누는 규칙도 AI 와 같아야 해서 목이 같이 씁니다 */
export { splitLines };
