// 제품 `frontend/src/shared/ui/TakeStatusBadge.tsx`에서 복사해 시작했다. 설계서가 관리한다.
import { Badge, type Tone } from './Badge';

/**
 * 목록의 상태 칸은 문구 길이와 관계없이 같은 크기와 중심을 유지한다.
 * 색은 Badge의 tone을 그대로 쓴다 — 같은 tone이 화면마다 다른 색이 되지 않게 색 표는 Badge 한 곳에만 둔다.
 */
export function TakeStatusBadge({ status }: { status: { label: string; tone: Tone } }) {
  return (
    <Badge
      tone={status.tone}
      className="inline-flex h-7 w-20 items-center justify-center whitespace-nowrap leading-none"
    >
      {status.label}
    </Badge>
  );
}
