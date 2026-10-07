/**
 * 슬라이드 대본을 줄 단위로 자르고, 키워드 자리를 강조 표시할 조각으로 나눕니다.
 *
 * ── 강조 위치는 어디서 오나 ─────────────────────────────────────────
 * AI 대본 나누기가 슬라이드마다 `keywords`(대본에 있는 표현 그대로)와 그 위치
 * `highlights`(content 안의 문자 위치, end 미포함)를 함께 줍니다. BE 는 그대로 전달합니다.
 *
 *   highlights 가 있으면 → 그 위치를 씁니다 (AI 가 정한 자리)
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

/** 키워드가 처음 나오는 자리. 못 찾으면 그 키워드는 뺍니다 */
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
  const source = highlights.length > 0 ? highlights : rangesFromKeywords(content, keywords);
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
