/**
 * 리허설 머리줄의 장식용 상태 표시. 서버·마이크 시작 게이트를 통과한
 * running 값만 전달받아, 연결 대기·종료 중에는 보이지 않습니다.
 */
export function StageLiveBadge({ running }: { running: boolean }) {
  return (
    <span className="stage-mission-label" data-live={running} aria-hidden="true">
      STAGE LIVE
    </span>
  );
}
