import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';

export type PitchOption = { value: string; label: string };

/**
 * Take 기록의 피치 고르기 (#1142). 기본 select 는 화살표가 테두리에 붙고 펼친 목록을 꾸밀 수 없어
 * 직접 그립니다. 상자 모양은 홈의 더보기(···) 메뉴와 같습니다.
 *
 * 키보드는 WAI-ARIA select-only combobox 방식입니다 — 초점은 늘 버튼에 두고, 가리킨 항목은
 * aria-activedescendant 로 알립니다. 닫혀 있으면 ↑ ↓ Enter Space 로 고른 항목에서, Home End 로
 * 처음 · 끝 항목에서 엽니다. 열려 있으면 ↑ ↓ Home End 로 옮기고 Enter · Space 로 고르며,
 * Esc 와 Tab 은 바꾸지 않고 닫습니다. 바깥을 눌러 초점이 나가도 닫습니다.
 *
 * `leading` 은 피치 목록 앞에 따로 두는 항목(전체 · 안내)이고 그 아래에 구분선을 긋습니다
 */
export function PitchSelect({
  label,
  value,
  leading,
  options,
  onChange,
}: {
  label: string;
  value: string;
  leading: PitchOption | null;
  options: PitchOption[];
  onChange: (value: string) => void;
}) {
  const items = leading ? [leading, ...options] : options;
  const selectedIndex = items.findIndex((item) => item.value === value);
  const selected = items[selectedIndex];
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const comboRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const labelId = useId();
  const listId = useId();
  const optionId = useId();

  // 목록 밖 포인터 입력으로 닫습니다. 목록 안의 터치·스크롤은 초점 변화와 관계없이 유지합니다.
  useEffect(() => {
    if (!open) return;
    function onOutsidePointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener('pointerdown', onOutsidePointerDown);
    return () => document.removeEventListener('pointerdown', onOutsidePointerDown);
  }, [open]);

  // 키보드로 옮긴 항목이 목록 밖이면 목록만 스크롤합니다. scrollIntoView 는 페이지까지 움직여서 쓰지 않습니다
  useEffect(() => {
    const list = listRef.current;
    const item = list?.querySelector<HTMLElement>('[data-active]');
    if (!list || !item) return;
    if (item.offsetTop < list.scrollTop) list.scrollTop = item.offsetTop;
    const bottom = item.offsetTop + item.offsetHeight;
    if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
  }, [open, active]);

  function openAt(index: number) {
    setActive(Math.max(0, index));
    setOpen(true);
  }

  function choose(index: number) {
    setOpen(false);
    const next = items[index];
    if (next && next.value !== value) onChange(next.value);
  }

  function onKeyDown(event: KeyboardEvent) {
    const last = items.length - 1;
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) openAt(selectedIndex);
      else if (event.key === 'Home') openAt(0);
      else if (event.key === 'End') openAt(last);
      else return;
      event.preventDefault();
      return;
    }

    if (event.key === 'ArrowDown') setActive(Math.min(active + 1, last));
    else if (event.key === 'ArrowUp') setActive(Math.max(active - 1, 0));
    else if (event.key === 'Home') setActive(0);
    else if (event.key === 'End') setActive(last);
    else if (event.key === 'Enter' || event.key === ' ') choose(active);
    else if (event.key === 'Escape') {
      // 펼친 목록만 닫습니다 — 바깥의 Esc 처리(대화창 등)까지 닿지 않게
      event.stopPropagation();
      setOpen(false);
    } else {
      if (event.key === 'Tab') setOpen(false);
      return;
    }
    event.preventDefault();
  }

  return (
    <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2 text-sm font-bold">
      <span id={labelId} onClick={() => comboRef.current?.focus()}>
        {label}
      </span>
      <div ref={containerRef} className="relative min-w-0 max-w-full flex-1 sm:max-w-xs">
        <div
          ref={comboRef}
          role="combobox"
          tabIndex={0}
          aria-labelledby={labelId}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          aria-activedescendant={open ? `${optionId}-${active}` : undefined}
          title={selected?.label}
          onClick={() => (open ? setOpen(false) : openAt(selectedIndex))}
          onKeyDown={onKeyDown}
          onBlur={(event) => {
            // 터치로 비포커스 옵션을 누르면 초점이 body로 빠질 수 있습니다.
            // 이 경우에는 클릭 선택을 먼저 처리하고, 실제 외부 포인터는 별도 리스너가 닫습니다.
            if (event.relatedTarget && event.relatedTarget !== document.body) setOpen(false);
          }}
          className="flex min-h-11 min-w-28 cursor-pointer select-none items-center gap-2 border-2 border-ink bg-white py-1 pl-2 pr-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-coral-deep aria-expanded:bg-cream"
        >
          <span className="min-w-0 flex-1 truncate">{selected?.label}</span>
          <svg
            aria-hidden="true"
            viewBox="0 0 12 12"
            className={`size-3 shrink-0 ${open ? 'rotate-180' : ''}`}
          >
            <path className="fill-none stroke-ink" strokeWidth="1.5" d="M2.5 4.5l3.5 3.5l3.5-3.5" />
          </svg>
        </div>
        {open && (
          <ul
            ref={listRef}
            id={listId}
            role="listbox"
            aria-labelledby={labelId}
            className="absolute inset-x-0 top-full z-20 mt-1 max-h-80 min-w-0 overflow-y-auto overscroll-contain border-2 border-ink bg-panel py-1 font-normal shadow-[3px_3px_0_var(--color-ink)]"
          >
            {items.map((item, index) => (
              <li
                key={item.value}
                id={`${optionId}-${index}`}
                role="option"
                aria-selected={index === selectedIndex}
                data-active={index === active ? '' : undefined}
                title={item.label}
                // 옵션 선택에만 적용해 초점을 유지합니다. ul의 스크롤바 드래그는 막지 않습니다.
                onMouseDown={(event) => {
                  if (event.button === 0) event.preventDefault();
                }}
                onClick={() => choose(index)}
                onMouseMove={() => {
                  if (index !== active) setActive(index);
                }}
                className={optionClass(
                  index === active,
                  index === selectedIndex,
                  leading !== null && index === 0 && options.length > 0,
                )}
              >
                <span className="min-w-0 flex-1 break-words [overflow-wrap:anywhere]">{item.label}</span>
                {index === selectedIndex && (
                  <svg aria-hidden="true" viewBox="0 0 14 14" className="size-3.5 shrink-0">
                    <path
                      className="fill-none stroke-coral-deep"
                      strokeWidth="2"
                      d="M2.5 7.5l3 3l6-7"
                    />
                  </svg>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** 가리킨 항목(마우스 · 키보드)은 cream, 고른 항목은 강조색 글자와 coral-wash 입니다. 가리키면 cream 이 이깁니다 */
function optionClass(active: boolean, selected: boolean, divided: boolean): string {
  const classes = ['flex min-h-11 cursor-pointer items-center gap-3 px-4 py-2'];
  if (active) classes.push('bg-cream');
  else if (selected) classes.push('bg-coral-wash');
  if (selected) classes.push('font-bold text-coral-deep');
  if (divided) classes.push('mb-1 border-b border-line');
  return classes.join(' ');
}
