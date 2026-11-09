// 제품 `frontend/src/shared/ui/CloseButton.tsx`에서 복사해 시작했다. 설계서가 관리한다.
import type { ButtonHTMLAttributes } from 'react';

export type CloseButtonProps = Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  'children' | 'className' | 'type'
>;

/** 창 · 패널의 글자 [닫기]. 모양과 문구만 맡고 닫는 동작은 onClick으로 받는다 */
export function CloseButton(props: CloseButtonProps) {
  return (
    <button
      {...props}
      type="button"
      className="inline-flex min-h-11 shrink-0 items-center justify-center whitespace-nowrap border border-line-strong px-5 text-sm font-bold hover:bg-cream disabled:cursor-not-allowed disabled:opacity-55"
    >
      닫기
    </button>
  );
}
