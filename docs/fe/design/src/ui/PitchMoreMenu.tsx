// 제품 `frontend/src/features/pitch/PitchMoreMenu.tsx`에서 복사해 시작했다. 설계서가 관리한다.
import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';

/** 메뉴와 ··· 버튼 사이 (px) */
const GAP = 4;

/**
 * 홈 피치 줄의 더보기(···) 메뉴 (#807). 지금은 삭제 하나뿐입니다.
 *
 * 키보드는 WAI-ARIA 메뉴 버튼 방식입니다 — Enter · Space · ↓ 로 열면 첫 항목, ↑ 면 마지막 항목에 초점이 가고,
 * ↑ ↓ Home End 로 옮기고, Esc 는 닫고 ··· 로 초점을 돌려줍니다. Tab 이나 바깥을 누르면 그냥 닫습니다.
 *
 * 목록 상자가 스크롤 상자(one-screen)라 메뉴를 그 안에 두면 마지막 줄에서 잘립니다. 그래서 화면 좌표(fixed)로
 * 띄우고, 아래 자리가 모자라면 위로 엽니다. 스크롤하거나 창 크기가 바뀌면 자리가 어긋나 닫습니다
 */
export function PitchMoreMenu({
  pitchTitle,
  blockedReason,
  onDelete,
  defaultOpen = false,
}: {
  pitchTitle: string;
  /** 있으면 삭제를 막고(aria-disabled) 이 이유를 항목 아래에 보입니다 */
  blockedReason: string | null;
  onDelete: () => void;
  /** 설계서 상태 미리보기용. 열린 채로 그리고 첫 항목에 초점을 준다 */
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState<'first' | 'last' | null>(defaultOpen ? 'first' : null);
  const [place, setPlace] = useState<{ top: number; right: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonId = useId();
  const menuId = useId();
  const labelId = useId();
  const reasonId = useId();

  const items = () => [
    ...(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []),
  ];
  const close = (returnFocus: boolean) => {
    setOpen(null);
    setPlace(null);
    if (returnFocus) buttonRef.current?.focus();
  };

  // 그린 뒤 메뉴 높이를 알아야 위 · 아래를 고릅니다. 그동안은 숨겨 둡니다(visibility)
  useLayoutEffect(() => {
    if (!open) return;
    const button = buttonRef.current?.getBoundingClientRect();
    const height = menuRef.current?.offsetHeight ?? 0;
    if (!button) return;
    const below = button.bottom + GAP;
    setPlace({
      top:
        below + height > window.innerHeight && button.top - GAP - height >= 0
          ? button.top - GAP - height
          : below,
      right: document.documentElement.clientWidth - button.right,
    });
  }, [open]);

  // 숨긴 항목은 초점을 받지 않아, 자리를 잡아 보인 다음 첫(마지막) 항목에 초점을 줍니다
  useLayoutEffect(() => {
    if (!open || !place) return;
    const list = items();
    (open === 'first' ? list[0] : list.at(-1))?.focus();
    // 자리는 열 때 한 번 잡습니다. 그 뒤로는 사용자가 옮긴 초점을 둡니다
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !buttonRef.current?.contains(target)) close(false);
    };
    const drop = () => close(false);
    document.addEventListener('pointerdown', outside);
    // 목록 상자 안의 스크롤도 잡도록 capture 로 듣습니다
    window.addEventListener('scroll', drop, true);
    window.addEventListener('resize', drop);
    return () => {
      document.removeEventListener('pointerdown', outside);
      window.removeEventListener('scroll', drop, true);
      window.removeEventListener('resize', drop);
    };
  }, [open]);

  const onButtonKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape' && open) {
      event.preventDefault();
      close(true);
      return;
    }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    setOpen(event.key === 'ArrowDown' ? 'first' : 'last');
  };

  const onMenuKey = (event: KeyboardEvent) => {
    const list = items();
    const at = list.indexOf(document.activeElement as HTMLElement);
    const move = (index: number) => {
      event.preventDefault();
      list[(index + list.length) % list.length]?.focus();
    };
    if (event.key === 'ArrowDown') move(at + 1);
    else if (event.key === 'ArrowUp') move(at - 1);
    else if (event.key === 'Home') move(0);
    else if (event.key === 'End') move(list.length - 1);
    else if (event.key === 'Escape') {
      event.preventDefault();
      close(true);
    } else if (event.key === 'Tab') close(false);
  };

  return (
    <>
      <button
        ref={buttonRef}
        id={buttonId}
        type="button"
        aria-label={`${pitchTitle} 더보기`}
        aria-haspopup="menu"
        aria-expanded={open !== null}
        aria-controls={open ? menuId : undefined}
        onClick={() => (open ? close(false) : setOpen('first'))}
        onKeyDown={onButtonKey}
        className="inline-flex h-9 w-9 shrink-0 items-center justify-center border-2 border-transparent text-sm font-bold tracking-widest hover:border-ink aria-expanded:border-ink aria-expanded:bg-cream"
      >
        <span aria-hidden="true">···</span>
      </button>
      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-labelledby={buttonId}
          onKeyDown={onMenuKey}
          onBlur={(event) => {
            // 창(확인 창)이나 다른 곳으로 초점이 나가면 닫습니다. 항목끼리 옮길 때와
            // ··· 를 다시 누를 때(그 click 이 닫습니다)는 둡니다
            const to = event.relatedTarget;
            if (!event.currentTarget.contains(to) && to !== buttonRef.current) close(false);
          }}
          style={place ?? { top: 0, right: 0, visibility: 'hidden' }}
          className="fixed z-20 w-64 border-2 border-ink bg-panel py-1 text-left shadow-[3px_3px_0_var(--color-ink)]"
        >
          <button
            type="button"
            role="menuitem"
            tabIndex={-1}
            aria-labelledby={labelId}
            aria-describedby={blockedReason ? reasonId : undefined}
            aria-disabled={blockedReason ? true : undefined}
            onClick={() => {
              if (blockedReason) return;
              close(true);
              onDelete();
            }}
            className="block w-full px-4 py-2 text-left -outline-offset-2 hover:bg-cream focus-visible:bg-cream aria-disabled:cursor-not-allowed aria-disabled:hover:bg-transparent"
          >
            <span
              id={labelId}
              className={`block text-sm font-bold ${blockedReason ? 'text-ink/80' : 'text-coral-deep'}`}
            >
              삭제
            </span>
            {blockedReason && (
              <span id={reasonId} className="mt-0.5 block text-xs leading-5 text-ink/80">
                {blockedReason}
              </span>
            )}
          </button>
        </div>
      )}
    </>
  );
}
