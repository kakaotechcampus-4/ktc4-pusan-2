/**
 * 대본 속 슬라이드 구분자 찾기 — "슬라이드 1" · "Slide 1" · "1." 처럼 **줄 맨 앞의 명시적 표기**만.
 *
 * AI script-parser(v1)가 대본을 나누는 규칙을 FE 에서 미리 따라 해 보는 것입니다.
 * 대본 입력 화면은 이걸로 "구분 3 / 8" 을 보여 주고, 목(`mocks/scripts.ts`)은 이걸로 나눕니다 —
 * 두 곳이 같은 규칙을 써야 화면에 보인 개수와 나뉜 결과가 어긋나지 않습니다.
 *
 * ★ 실제 AI 는 LLM 이라 "[슬라이드 1]" 처럼 조금 다른 표기도 알아들을 수 있습니다.
 *   그래서 이 결과는 **안내용**이고, 이걸로 매핑을 막지는 않습니다.
 *   "첫째", "다음으로" 같은 전환 표현과 빈 줄은 AI 도 구분자로 보지 않습니다.
 */

export const SLIDE_MARKER = /^\s*(?:(?:slide|슬라이드)\s*(\d+)|(\d+)\s*[.)])\s*[:.\-–)]?\s*/i;

/** 이 줄이 구분자로 시작하면 그 번호, 아니면 null */
export function markerNumber(line: string): number | null {
  const m = SLIDE_MARKER.exec(line);
  return m ? Number(m[1] ?? m[2]) : null;
}

export interface MarkerScan {
  /** 찾은 구분자의 번호, 나온 순서대로 */
  numbers: number[];
  /**
   * 첫 구분자 **앞에** 구분되지 않은 글이 있나. 있으면 AI 는 대본 전체를 나누지 않습니다
   * ("일부 구간에만 구분이 있으면 fail").
   *
   * 맨 위 **한 줄**은 세지 않습니다 — AI 는 "대본 최상단에 제목이 있으면 제거"하고 나눕니다
   * (`ai/research/script-parser/script_parser/prompt.py` 전처리 규칙). 두 줄 이상이면 제목이 아니라
   * 구분 없는 본문으로 봅니다.
   */
  leadingText: boolean;
}

export function scanSlideMarkers(text: string): MarkerScan {
  const numbers: number[] = [];
  let leadingLines = 0;
  for (const line of text.split('\n')) {
    const n = markerNumber(line);
    if (n !== null) numbers.push(n);
    else if (numbers.length === 0 && line.trim() !== '') leadingLines++;
  }
  return { numbers, leadingText: leadingLines > 1 };
}
