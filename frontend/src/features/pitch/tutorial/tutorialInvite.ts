import type { HomePitch, HomeTake } from '@/types/home';

/**
 * 홈의 [튜토리얼 해 보기]를 움직여 권할지 (#931). 샘플 피치로 끝까지 연습한 Take 가 없으면 권합니다 —
 * 샘플을 아직 받지 않았거나, 받았지만 장치 점검에서 멈췄거나, 연습이 중간에 끊긴 사람입니다.
 *
 * 튜토리얼 리포트를 이미 받았으면(`reportReceived`, 내 정보의 `tutorial_report_at`) 권하지 않습니다.
 * 리포트를 받은 뒤 샘플을 지운 사람은 홈 목록만으로는 가릴 수 없어서 함께 봅니다.
 */
export function invitesTutorial(pitches: readonly HomePitch[], reportReceived: boolean): boolean {
  if (reportReceived) return false;
  const sample = pitches.find((pitch) => pitch.is_demo);
  return !sample?.takes.some(finishedByUser);
}

/**
 * 사용자가 끝까지 연습한 Take — 분석 중 · 완료, 그리고 끝냈지만 분석에 실패한 것.
 * 시작하지 않았거나 끊긴 Take 는 `FAILED` 에 끝난 이유(`ABANDONED` · `CLOSED`)가 붙습니다
 */
function finishedByUser(take: HomeTake): boolean {
  if (take.status === 'ANALYZING' || take.status === 'COMPLETED') return true;
  return (
    take.status === 'FAILED' && take.end_reason !== 'ABANDONED' && take.end_reason !== 'CLOSED'
  );
}
