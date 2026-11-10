// #1143 Take 기록의 select-only combobox를 정적 설계서에서 재현한다. URL·API는 변경하지 않는다.
import { useId, useState, type KeyboardEvent } from 'react';

export function PitchSelectPreview({
  comparing,
  pitchTitle,
}: {
  comparing: boolean;
  pitchTitle: string;
}) {
  const labelId = useId();
  const listId = useId();
  const options = comparing
    ? [{ value: 'p1', label: pitchTitle }]
    : [
        { value: '', label: '전체' },
        { value: 'p1', label: pitchTitle },
      ];
  const [selected, setSelected] = useState(comparing ? 'p1' : '');
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === selected));
  const selectedOption = options[selectedIndex]!;

  function choose(index: number) {
    setSelected(options[index]!.value);
    setActive(index);
    setOpen(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      if (open) {
        event.preventDefault();
        setOpen(false);
      }
      return;
    }
    if (event.key === 'Tab') {
      setOpen(false);
      return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (open) choose(active);
      else {
        setActive(selectedIndex);
        setOpen(true);
      }
      return;
    }
    let direction = 0;
    if (event.key === 'ArrowDown') direction = 1;
    else if (event.key === 'ArrowUp') direction = -1;
    if (direction || event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      setOpen(true);
      setActive((current) => {
        if (event.key === 'Home') return 0;
        if (event.key === 'End') return options.length - 1;
        return Math.min(
          options.length - 1,
          Math.max(0, (open ? current : selectedIndex) + direction),
        );
      });
    }
  }

  return (
    <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2 text-sm font-bold">
      <span id={labelId}>피치</span>
      <div className="relative min-w-0 max-w-full flex-1 sm:w-xs sm:flex-none">
        <div
          role="combobox"
          tabIndex={0}
          aria-labelledby={labelId}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          aria-activedescendant={open ? `${listId}-option-${active}` : undefined}
          title={selectedOption.label}
          onClick={() => {
            setActive(selectedIndex);
            setOpen((value) => !value);
          }}
          onKeyDown={onKeyDown}
          onBlur={() => setOpen(false)}
          className="flex min-h-11 min-w-28 cursor-pointer select-none items-center gap-2 border-2 border-ink bg-white py-1 pl-2 pr-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-coral-deep aria-expanded:bg-cream"
        >
          <span className="min-w-0 flex-1 truncate">{selectedOption.label}</span>
          <svg
            aria-hidden="true"
            viewBox="0 0 12 12"
            className={`size-3 shrink-0 ${open ? 'rotate-180' : ''}`}
          >
            <path
              className="fill-none stroke-ink"
              strokeWidth="1.5"
              d="M2.5 4.5l3.5 3.5l3.5-3.5"
            />
          </svg>
        </div>
        {open && (
          <ul
            id={listId}
            role="listbox"
            aria-labelledby={labelId}
            onMouseDown={(event) => event.preventDefault()}
            className="absolute inset-x-0 top-full z-20 mt-1 max-h-80 min-w-0 overflow-y-auto border-2 border-ink bg-panel py-1 font-normal shadow-[3px_3px_0_var(--color-ink)]"
          >
            {options.map((option, index) => (
              <li
                key={option.value}
                id={`${listId}-option-${index}`}
                role="option"
                aria-selected={index === selectedIndex}
                data-active={index === active ? '' : undefined}
                title={option.label}
                onMouseMove={() => setActive(index)}
                onClick={() => choose(index)}
                className={optionClass(index, active, selectedIndex, !comparing)}
              >
                <span className="min-w-0 flex-1 break-words [overflow-wrap:anywhere]">
                  {option.label}
                </span>
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

function optionClass(index: number, active: number, selected: number, showAll: boolean) {
  const classes = ['flex min-h-11 cursor-pointer items-center gap-3 px-4 py-2'];
  if (index === active) classes.push('bg-cream');
  else if (index === selected) classes.push('bg-coral-wash');
  if (index === selected) classes.push('font-bold text-coral-deep');
  if (showAll && index === 0) classes.push('mb-1 border-b border-line');
  return classes.join(' ');
}
