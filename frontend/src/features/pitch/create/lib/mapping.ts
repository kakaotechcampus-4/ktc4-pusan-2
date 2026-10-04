/**
 * 대본을 슬라이드 장수만큼의 블록으로 나눕니다.
 *
 * ★ **임시 규칙입니다.** 실제 매핑은 AI(`script-parser`)가 슬라이드 내용과 대본을
 *   맞춰 보고 정하며, 그 결과 형식이 아직 확정되지 않았습니다. 화면이 요구하는 것은
 *   "슬라이드 장수와 같은 길이의 문자열 배열" 하나뿐이라, 서버가 붙으면 이 함수만
 *   갈아 끼웁니다.
 *
 * 지금 규칙: 빈 줄로 나뉜 문단을 우선하고, 문단이 장수보다 적으면 줄 단위로 나눕니다.
 * 그래도 모자라면 뒤쪽 슬라이드는 빈 블록입니다 — 장수와 블록 수가 맞아야 합니다.
 */
export function splitScript(text: string, slideCount: number): string[] {
  const clean = (parts: string[]) => parts.map((p) => p.trim()).filter(Boolean);

  const paragraphs = clean(text.split(/\n{2,}/));
  const units = paragraphs.length >= slideCount ? paragraphs : clean(text.split(/\n/));

  if (units.length <= slideCount) {
    return Array.from({ length: slideCount }, (_, i) => units[i] ?? '');
  }

  // 단위가 장수보다 많으면 남는 것은 마지막 슬라이드에 이어 붙입니다 — 글이 사라지면 안 됩니다
  const head = units.slice(0, slideCount - 1);
  return [...head, units.slice(slideCount - 1).join('\n')];
}
