/**
 * 다시 평가 확인 카드의 바뀌는 내용 (AI `ask_confirm_card`: 전 · 후 = `{take_id, take_number, script_used | targets}`).
 * 대상 Take 는 번호로 한 번, 바꾸는 조건은 우리말로 보입니다. Take ID 는 보이지 않고, 모르는 칸은 받은 그대로
 */
export function reassessRows(
  before: Record<string, unknown> | null,
  after: Record<string, unknown>,
): { key: string; label: string; value: string }[] {
  const rows: { key: string; label: string; value: string }[] = [];
  const both = (k: string) =>
    before ? `${show(k, before[k])} → ${show(k, after[k])}` : show(k, after[k]);
  for (const k of new Set([...Object.keys(before ?? {}), ...Object.keys(after)])) {
    if (k === 'take_id') continue;
    if (k === 'take_number') {
      rows.push({ key: k, label: '대상', value: `Take ${plain(after[k] ?? before?.[k])}` });
      continue;
    }
    rows.push({ key: k, label: REASSESS_KEY_LABEL[k] ?? k, value: both(k) });
  }
  return rows;
}

const REASSESS_KEY_LABEL: Record<string, string> = {
  script_used: '대본 보며 발표',
  targets: '내 평가 기준',
};

function show(key: string, v: unknown) {
  if (key === 'script_used' && typeof v === 'boolean') return v ? '예' : '아니요';
  if (key === 'targets' && Array.isArray(v)) return `${v.length}개`;
  return plain(v);
}

/** 바뀌는 내용 한 줄 — 값은 받은 그대로 글자로 */
export const plain = (v: unknown) => {
  if (v === null || v === undefined) return '없음';
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v);
  return JSON.stringify(v);
};
