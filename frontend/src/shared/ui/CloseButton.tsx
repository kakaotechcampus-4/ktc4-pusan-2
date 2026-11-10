import type { ButtonHTMLAttributes } from 'react';

export type CloseButtonProps = Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  'children' | 'className' | 'type'
>;

/**
 * 창 · 패널의 글자 [닫기]. 모양과 문구만 맡고 닫는 동작은 onClick 으로 받습니다.
 * `<dialog>` 는 `ref.current?.close()` 를 넘깁니다 — 창이 문서에서 빠지기 전에 닫아야 브라우저가
 * 초점을 연 버튼으로 돌려줍니다 (#766 · #771)
 * 아이콘만 있는 닫기(새미나이 패널 머리글의 ×)는 그 머리글의 아이콘 버튼 모양을 따르므로 쓰지 않습니다.
 */
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
