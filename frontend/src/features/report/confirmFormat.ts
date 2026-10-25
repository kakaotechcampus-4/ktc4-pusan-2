import { formatDuration } from '@/shared/lib/clock';
import type { ConfirmAnswer, ConfirmationItem, ConfirmationsRequest } from '@/types/confirmation';

/** 항목마다 고른 것. `unsure` 는 보내지 않고 점수에서 빠진 채 남습니다 (계약 6-3) */
export type Choice = ConfirmAnswer | 'unsure';

/** `item_id` 는 장마다 R1 부터 다시 세서 장 번호와 함께 키로 씁니다 */
export function itemKey(item: Pick<ConfirmationItem, 'slide_number' | 'item_id'>) {
  return `${item.slide_number}:${item.item_id}`;
}

/** 고른 것을 보낼 답으로 — 고르지 않았거나 '잘 모르겠어요'인 항목은 뺍니다 */
export function toAnswers(
  items: ConfirmationItem[],
  picks: Record<string, Choice>,
): ConfirmationsRequest['answers'] {
  return items.flatMap((item) => {
    const pick = picks[itemKey(item)];
    if (pick === undefined || pick === 'unsure') return [];
    return [{ slide_number: item.slide_number, item_id: item.item_id, answer: pick }];
  });
}

/** 모든 항목에 하나씩 골랐나 ('잘 모르겠어요' 포함) */
export function allPicked(items: ConfirmationItem[], picks: Record<string, Choice>) {
  return items.every((item) => picks[itemKey(item)] !== undefined);
}

/** 남은 시간 → `09:30`. 초는 올림이라 0 이 되는 순간에만 `00:00` 입니다 */
export function remainingLabel(ms: number) {
  return formatDuration(Math.max(0, Math.ceil(ms / 1000) * 1000));
}

/**
 * 문장 안에서 강조할 말을 찾아 [앞, 말, 뒤] 로 나눕니다. 문장이 없거나 말이 문장에 없으면 null —
 * 그때 화면은 말만 보여 줍니다.
 */
export function splitAround(sentence: string | null, text: string) {
  if (sentence === null || text === '') return null;
  const at = sentence.indexOf(text);
  if (at < 0) return null;
  return [sentence.slice(0, at), text, sentence.slice(at + text.length)] as const;
}
