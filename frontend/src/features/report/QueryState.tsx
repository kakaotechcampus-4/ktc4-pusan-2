import type { UseQueryResult } from '@tanstack/react-query';
import { Button } from '@/shared/ui/Button';
import { Card } from '@/shared/ui/Panel';

/** 불러오는 중 · 실패 한 칸. 데이터가 왔으면 아무것도 그리지 않습니다 */
export function QueryState({ query }: { query: UseQueryResult<unknown> }) {
  if (query.isPending)
    return (
      <Card>
        <p role="status" className="text-stone">
          연습 기록을 가져오는 중이에요…
        </p>
      </Card>
    );
  if (query.isError)
    return (
      <Card>
        <div role="alert" className="flex flex-col gap-3">
          <p className="font-bold">연습 기록을 가져오지 못했어요.</p>
          <p className="text-sm text-stone">잠시 후 다시 시도해 주세요.</p>
          <div>
            <Button variant="secondary" onClick={() => query.refetch().catch(() => undefined)}>
              다시 불러오기
            </Button>
          </div>
        </div>
      </Card>
    );
  return null;
}
