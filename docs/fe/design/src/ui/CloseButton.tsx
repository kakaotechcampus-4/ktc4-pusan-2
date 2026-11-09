// 제품 `frontend/src/shared/ui/CloseButton.tsx`에서 복사해 시작했다. 설계서가 관리한다.
import type { ButtonHTMLAttributes } from 'react';

export interface CloseButtonProps extends Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  'children' | 'className' | 'type'
> {
  /** 옆 버튼들이 좁은 머리글(채팅 패널)에서 그 버튼들과 같은 가로 여백을 쓴다 */
  compact?: boolean;
}

/** 창 · 패널의 [닫기]. 모양과 문구만 맡고 닫는 동작은 onClick으로 받는다 */
export function CloseButton({ compact = false, ...props }: CloseButtonProps) {
  const spacing = compact ? 'min-w-11 px-2' : 'px-5';
  return (
    <button
      {...props}
      type="button"
      className={`inline-flex min-h-11 shrink-0 items-center justify-center whitespace-nowrap border border-line-strong text-sm font-bold hover:bg-cream disabled:cursor-not-allowed disabled:opacity-55 ${spacing}`}
    >
      닫기
    </button>
  );
}
