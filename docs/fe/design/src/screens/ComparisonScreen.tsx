// 제품 Take 비교(`frontend/src/features/report/ComparisonPage.tsx`)의 설계서 샘플.
import { Button } from '@/ui/Button';
import { Card } from '@/ui/Panel';
import { Mascot } from '@/ui/Mascot';
import { Frame, QueryNotice, PITCH, type Navigate } from './shared';

export function ComparisonScreen({
  stateId,
  onNavigate,
}: {
  stateId: string;
  onNavigate: Navigate;
}) {
  return (
    <Frame title="Take 비교">
      {stateId === 'loading' && (
        <QueryNotice stateId="loading" onRetry={() => onNavigate('comparison', 'default')} />
      )}
      {stateId === 'error' && (
        <Card>
          <div role="alert" className="flex flex-col gap-3">
            <p className="font-bold">피치를 찾을 수 없어요.</p>
            <p className="text-sm text-stone">지워졌거나 열 수 없는 피치예요.</p>
            <div>
              <Button onClick={() => onNavigate('home')}>내 피치로</Button>
            </div>
          </div>
        </Card>
      )}
      {(stateId === 'default' || stateId === 'empty') && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
          <p className="min-w-0 font-bold">{PITCH}</p>
          <p className="tabular text-sm text-stone">Take {stateId === 'empty' ? 1 : 2}개</p>
        </div>
      )}
      {stateId === 'empty' && (
        <Card>
          <Mascot who="pico">비교하려면 Take가 2개 이상 필요해요. 한 번 더 연습해 볼까요?</Mascot>
        </Card>
      )}
      {stateId === 'default' && (
        <Card title="지표 흐름">
          <div role="tablist" aria-label="비교 지표" className="flex flex-wrap gap-1.5">
            <button
              type="button"
              role="tab"
              aria-selected
              className="border-2 border-ink bg-coral-wash px-3 py-1.5 text-sm font-bold"
            >
              청중 응시
            </button>
          </div>
          <ul className="flex flex-col gap-2 text-sm">
            <li>
              TAKE 1 · <span className="text-stone">—</span>
            </li>
            <li>TAKE 2 · 68%</li>
          </ul>
          <p className="text-xs text-ink/80">
            높을수록 좋아요. 점선은 측정하지 못했거나 시선 엔진이 달라 비교하지 않는 Take예요.
          </p>
        </Card>
      )}
    </Frame>
  );
}
