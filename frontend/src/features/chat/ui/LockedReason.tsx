/** 버튼 안에 넣는 숨은 이유 — 이유가 없으면 그리지 않습니다 */
export function LockedReason({ reason }: { reason: string | null | undefined }) {
  if (!reason) return null;
  return <span className="sr-only"> ({reason})</span>;
}
