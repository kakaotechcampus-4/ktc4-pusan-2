import { MAX_CRITERIA, type SkippedCriterion } from './draft';

/**
 * 자유롭게 적은 평가기준을 항목으로 정리합니다.
 *
 * ★ **임시 규칙입니다.** 실제로는 AI 가 정리하고(`ai/` 의 standards 파서), 그 응답 형식이
 *   아직 정해지지 않았습니다. 화면이 요구하는 모양 — `정리된 항목` 과 `반영하지 못한 문장 +
 *   이유 + 예시` — 만 먼저 고정해 두었고, 서버가 붙으면 이 함수만 갈아 끼웁니다.
 *
 * 여기서 하는 일은 셋뿐입니다.
 *   1. 문장·쉼표·목록 기호로 나눈다
 *   2. 말끝("…싶어요", "…할래요")을 "…하기" 로 다듬는다
 *   3. 평가할 행동이 안 보이는 문장은 항목으로 만들지 않고 이유를 돌려준다
 */

export interface Organized {
  items: string[];
  skipped: SkippedCriterion[];
}

const VAGUE = /멋지게|멋있게|잘 ?하|잘하고|좋게|자신감|완벽|느낌|열심히|훌륭/;
/** 숫자·횟수·구간·"~하지 않고" 처럼 지켜졌는지 셀 수 있는 단서 */
const CHECKABLE = /\d|[가-힣]*(번|회|초|분|개|장)|않고|없이|말하|언급|소개|요약|설명|출처/;

const VAGUE_REASON = '평가할 행동이 구체적이지 않아 반영하지 못했어요.';
const VAGUE_EXAMPLE = '예: 결론에서 핵심 내용을 한 문장으로 요약하기';
const OVER_REASON = `기준은 최대 ${MAX_CRITERIA}개까지 반영돼요.`;
const OVER_EXAMPLE = '예: 가장 중요한 기준부터 남기고 나머지는 다음 버전에 적기';

function split(source: string): string[] {
  return source
    .split(/[\n.!?。]+/)
    .flatMap((line) => line.split(/,|，/))
    .map((s) => s.replace(/^\s*(?:[-*•·]|\d+[.)])\s*/, '').trim())
    .filter(Boolean);
}

/** 말끝을 "…기" 로 맞춥니다. 모르는 말끝은 그대로 둡니다 */
export function toAction(sentence: string): string {
  return sentence
    .replace(/고 싶(?:어요|습니다|어)$/, '기')
    .replace(/(?:할래요|할게요|하겠습니다|합니다|해요)$/, '하기')
    .replace(/고$/, '기')
    .replace(/\s+/g, ' ')
    .trim();
}

export function organizeCriteria(source: string): Organized {
  const items: string[] = [];
  const skipped: SkippedCriterion[] = [];

  for (const sentence of split(source)) {
    if (VAGUE.test(sentence) && !/\d/.test(sentence)) {
      skipped.push({ text: sentence, reason: VAGUE_REASON, example: VAGUE_EXAMPLE });
      continue;
    }
    if (!CHECKABLE.test(sentence)) {
      skipped.push({ text: sentence, reason: VAGUE_REASON, example: VAGUE_EXAMPLE });
      continue;
    }
    if (items.length >= MAX_CRITERIA) {
      skipped.push({ text: sentence, reason: OVER_REASON, example: OVER_EXAMPLE });
      continue;
    }
    items.push(toAction(sentence));
  }

  return { items, skipped };
}
