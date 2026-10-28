/**
 * 슬라이드 대본을 줄 단위로 자르고, 키워드 자리를 강조 표시할 조각으로 나눕니다.
 *
 * ── 강조 위치는 어디서 오나 ─────────────────────────────────────────
 * AI 대본 나누기가 슬라이드마다 `keywords`(대본에 있는 표현 그대로)와 그 위치
 * `highlights`(content 안의 문자 위치, end 미포함)를 함께 줍니다. BE 는 그대로 전달합니다.
 *
 *   highlights 가 있으면 → 그 위치를 UTF-16 위치로 바꿔 씁니다 (AI 가 정한 자리)
 *   없고 keywords 만 있으면 → 각 키워드가 **처음 나오는 자리**를 찾아 씁니다
 *                              (AI 의 find_highlights 와 같은 규칙. 못 찾은 키워드는 건너뜁니다)
 *
 * ── 왜 줄로 자르면서 위치를 지키나 ─────────────────────────────────
 * 화면은 줄마다 한 문단으로 그립니다. 위치는 content 전체 기준이라, 줄로 먼저 자르면
 * 위치가 어긋납니다. 그래서 각 줄이 content 의 어디서 시작하는지 들고 다니며 자릅니다.
 */

export interface TextRange {
  start: number;
  end: number;
}

export interface MarkPart {
  text: string;
  mark: boolean;
}

/**
 * API 의 문자 위치(유니코드 코드 포인트)를 JS 문자열 위치(UTF-16 코드 단위)로 바꿉니다.
 *
 * AI 는 Python 이라 `highlights` 를 코드 포인트로 셉니다 — 😀 한 글자가 1칸입니다.
 * JS 의 `slice` 는 UTF-16 으로 세서 같은 글자가 2칸입니다(보조 평면 문자, 서로게이트 쌍).
 * 그대로 쓰면 이모지가 하나 앞에 있을 때마다 강조가 한 칸씩 밀립니다
 * ("😀 핵심" 에 AI 의 `2:4` 를 그대로 넣으면 "핵심" 대신 " 핵"이 강조됩니다).
 *
 * 한글 · 영문처럼 기본 평면 글자만 있으면 두 단위가 같아 값이 그대로입니다.
 * 결합 이모지(👨‍👩‍👧)는 여러 코드 포인트라 Python 도 여러 칸으로 세고, 여기서도 코드 포인트마다
 * 바꾸므로 맞습니다. 대본 밖을 가리키는 위치는 끝으로 붙입니다 — 정리는 `normalizeRanges` 가 합니다.
 */
export function codePointRangesToUtf16(content: string, ranges: readonly TextRange[]): TextRange[] {
  // offsets[i] = 코드 포인트 i 가 시작하는 UTF-16 위치. 마지막 칸은 문자열 끝입니다
  const offsets: number[] = [];
  let at = 0;
  for (const ch of content) {
    offsets.push(at);
    at += ch.length;
  }
  offsets.push(at);
  const toUtf16 = (i: number) => offsets[Math.min(Math.max(0, Math.floor(i)), offsets.length - 1)];
  return ranges.map((r) => ({ start: toUtf16(r.start), end: toUtf16(r.end) }));
}

/**
 * 키워드가 처음 나오는 자리. 못 찾으면 그 키워드는 뺍니다.
 * JS 의 `indexOf` 로 찾으므로 처음부터 UTF-16 위치입니다 — 바꾸지 않습니다.
 */
export function rangesFromKeywords(content: string, keywords: readonly string[]): TextRange[] {
  const ranges: TextRange[] = [];
  for (const keyword of keywords) {
    const word = keyword.trim();
    if (!word) continue;
    const start = content.indexOf(word);
    if (start >= 0) ranges.push({ start, end: start + word.length });
  }
  return ranges;
}

/**
 * 범위를 content 안으로 자르고, 정렬하고, 겹치면 합칩니다.
 * AI 가 낸 값이라도 믿지 않습니다 — 범위 하나가 틀려도 대본이 깨지면 안 됩니다.
 */
export function normalizeRanges(ranges: readonly TextRange[], length: number): TextRange[] {
  const clean = ranges
    .map((r) => ({
      start: Math.max(0, Math.floor(r.start)),
      end: Math.min(length, Math.floor(r.end)),
    }))
    .filter((r) => Number.isFinite(r.start) && Number.isFinite(r.end) && r.end > r.start)
    .sort((a, b) => a.start - b.start);

  const merged: TextRange[] = [];
  for (const r of clean) {
    const last = merged.at(-1);
    if (last && r.start <= last.end) last.end = Math.max(last.end, r.end);
    else merged.push({ ...r });
  }
  return merged;
}

/**
 * 줄마다 조각 목록. 빈 줄은 빠지고, 줄 앞뒤 공백은 잘립니다 (강조 위치는 그대로 맞습니다).
 */
export function markScriptLines(
  content: string,
  highlights: readonly TextRange[],
  keywords: readonly string[],
): MarkPart[][] {
  // AI 가 준 위치만 단위를 바꿉니다. 키워드로 찾은 위치는 이미 JS 단위입니다
  const source =
    highlights.length > 0
      ? codePointRangesToUtf16(content, highlights)
      : rangesFromKeywords(content, keywords);
  const ranges = normalizeRanges(source, content.length);

  const lines: MarkPart[][] = [];
  const lineRe = /[^\n]+/g;
  for (let m = lineRe.exec(content); m !== null; m = lineRe.exec(content)) {
    const raw = m[0];
    const lead = raw.length - raw.trimStart().length;
    const text = raw.trim();
    if (!text) continue;

    const lineStart = m.index + lead;
    const lineEnd = lineStart + text.length;
    const parts: MarkPart[] = [];
    let cursor = lineStart;
    for (const r of ranges) {
      if (r.end <= lineStart || r.start >= lineEnd) continue;
      const s = Math.max(r.start, lineStart);
      const e = Math.min(r.end, lineEnd);
      if (s > cursor) parts.push({ text: content.slice(cursor, s), mark: false });
      parts.push({ text: content.slice(s, e), mark: true });
      cursor = e;
    }
    if (cursor < lineEnd) parts.push({ text: content.slice(cursor, lineEnd), mark: false });
    lines.push(parts);
  }
  return lines;
}
