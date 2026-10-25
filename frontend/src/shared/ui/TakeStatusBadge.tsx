import type { TakeStatusView } from '@/shared/lib/takeStatus';
import { Badge } from './Badge';

/**
 * 목록의 상태 칸은 문구 길이와 관계없이 같은 크기와 중심을 유지합니다.
 * 색은 Badge 의 tone 을 그대로 씁니다 — 같은 tone 이 화면마다 다른 색이 되지 않게 색 표는 Badge 한 곳에만 둡니다.
 */
export function TakeStatusBadge({ status }: { status: Pick<TakeStatusView, 'label' | 'tone'> }) {
  return (
    <Badge
      tone={status.tone}
      className="inline-flex h-7 w-20 items-center justify-center whitespace-nowrap leading-none"
    >
      {status.label}
    </Badge>
  );
}
