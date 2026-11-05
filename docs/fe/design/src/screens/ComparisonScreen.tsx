/** 이전 P7 미리보기도 통합된 Take 기록의 지표 비교 화면을 사용합니다. */
import { TakesScreen } from './TakesScreen';
import type { Navigate } from './shared';

export function ComparisonScreen({
  stateId,
  onNavigate,
}: {
  stateId: string;
  onNavigate: Navigate;
}) {
  return <TakesScreen stateId={stateId === 'empty' ? 'compare-empty' : 'compare'} onNavigate={onNavigate} />;
}
