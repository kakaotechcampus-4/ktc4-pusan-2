import type { ButtonHTMLAttributes } from 'react';

export interface CloseButtonProps extends Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  'children' | 'className' | 'type'
> {
  /** 옆 버튼들이 좁은 머리글(채팅 패널)에서 그 버튼들과 같은 가로 여백을 씁니다 */
  compact?: boolean;
}

/**
 * 창 · 패널의 [닫기]. 모양과 문구만 맡고 닫는 동작은 onClick 으로 받습니다.
 * `<dialog>` 는 `ref.current?.close()` 를 넘깁니다 — 창이 문서에서 빠지기 전에 닫아야 브라우저가
 * 초점을 연 버튼으로 돌려줍니다 (#766 · #771)
 */
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
