import type { ReactNode } from 'react';

/** 상태 문장과 장치 선택/버튼을 분리해, 줄 전체를 누르는 동작을 없앱니다. */
export function CheckCard({
  title,
  rows,
  action,
  children,
}: {
  title: string;
  rows: {
    id: string;
    name: string;
    label: ReactNode;
    selection?: ReactNode;
    done: boolean;
    action?: { text: string; onClick: () => void; disabled?: boolean; primary?: boolean } | null;
    trailing?: ReactNode;
    optional?: boolean;
  }[];
  action?: ReactNode;
  children?: ReactNode;
}) {
  const required = rows.filter((row) => !row.optional);
  const done = required.filter((row) => row.done).length;
  return (
    <section
      className="prepare-equipment border border-line bg-panel px-3 py-2.5 prepare-compact:px-2.5 prepare-compact:py-1.5"
      aria-label={title}
    >
      <div className="[&>h2]:basis-20! max-[600px]:[&>h2]:basis-[72px]! [&>button]:ml-auto prepare-section-heading flex min-h-6 flex-wrap items-center gap-x-2.5 gap-y-1.5 text-xs leading-[18px] [&>h2]:text-[15px] [&>h2]:leading-[22px] [&>h2]:font-bold">
        <h2>{title}</h2>
        <span className="prepare-required-status inline-flex items-center gap-1">
          <svg
            width="16"
            height="16"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            aria-hidden="true"
          >
            <circle cx="10" cy="10" r="8" />
            {done === required.length && <path d="m6 10 3 3 5-6" />}
          </svg>
          필수 {done}/{required.length} {done === required.length ? '완료' : '준비'}
        </span>
        {action}
      </div>
      <ul className="prepare-checks mt-1">
        {rows.map((row) => (
          <li
            key={row.id}
            className="prepare-check grid h-10 grid-cols-[80px_minmax(0,1fr)_50%] items-center gap-2 border-t border-line text-xs prepare-compact:h-[34px] max-[600px]:h-auto max-[600px]:min-h-[72px] max-[600px]:grid-cols-[72px_minmax(0,1fr)] max-[600px]:gap-y-1 max-[600px]:py-1.5"
            data-done={row.done}
          >
            <span className="prepare-check-name flex flex-col items-start text-[13px] leading-4 font-semibold max-[600px]:col-start-1 max-[600px]:row-span-2 max-[600px]:row-start-1 [&>small]:text-[10px] [&>small]:leading-3 [&>small]:font-normal">
              {row.name}
              {row.optional && <small>선택</small>}
            </span>
            <div className="prepare-check-status flex min-w-0 items-center gap-1 leading-4 wrap-anywhere [&>svg]:shrink-0 max-[600px]:col-start-2 max-[600px]:row-start-1">
              <svg
                width="16"
                height="16"
                viewBox="0 0 20 20"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                aria-hidden="true"
              >
                <circle cx="10" cy="10" r="8" />
                {row.done && <path d="m6 10 3 3 5-6" />}
              </svg>
              {row.label}
              {row.trailing && (
                <div className="prepare-check-meter w-16 shrink-0">{row.trailing}</div>
              )}
            </div>
            <div className="prepare-check-action flex min-w-0 items-center justify-end [&>button]:w-[118px] [&>label]:w-full [&>label]:min-w-0 max-[600px]:col-start-2 max-[600px]:row-start-2">
              {row.selection}
              {row.action && (
                <button
                  type="button"
                  className={`prepare-action inline-flex min-h-8 shrink-0 items-center justify-center gap-1 border border-ink bg-panel px-2.5 py-1 text-xs leading-[18px] font-semibold text-ink hover:bg-cream disabled:pointer-events-none disabled:cursor-default disabled:border-dashed disabled:border-line-strong disabled:bg-cream disabled:text-ink prepare-low:min-h-7 prepare-low:py-0.5 ${row.action.primary ? 'bg-coral-deep! text-white! hover:bg-ink! disabled:bg-cream! disabled:text-ink! disabled:border-line-strong!' : ''}`}
                  disabled={row.action.disabled}
                  onClick={row.action.onClick}
                >
                  {row.action.text}
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
      {children}
    </section>
  );
}
