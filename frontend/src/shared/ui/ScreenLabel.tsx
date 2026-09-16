/**
 * 화면 목록 문서의 머리말. 시안 위쪽에 붙어 있는 그 줄입니다.
 *
 * 제품 UI가 아니라 **대조용**입니다 — 시안과 화면을 나란히 놓고 번호로 맞춥니다.
 * 걷어낼 때는 이 컴포넌트를 쓰는 곳만 지우면 됩니다.
 */
export function ScreenLabel({
  screenNo,
  screenName,
  entry,
}: {
  screenNo: string;
  screenName: string;
  entry: string;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-3 px-1">
      <span className="tabular font-mono text-xs font-bold text-stone">{screenNo}</span>
      <h1 className="text-lg font-bold">{screenName}</h1>
      <span className="text-xs text-stone">{entry}</span>
    </div>
  );
}
