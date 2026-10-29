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
    <section className="prepare-equipment" aria-label={title}>
      <div className="prepare-section-heading">
        <h2>{title}</h2>
        <span className="prepare-required-status">
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
      <ul className="prepare-checks">
        {rows.map((row) => (
          <li key={row.id} className="prepare-check" data-done={row.done}>
            <span className="prepare-check-name">
              {row.name}
              {row.optional && <small>선택</small>}
            </span>
            <div className="prepare-check-status">
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
              {row.trailing && <div className="prepare-check-meter">{row.trailing}</div>}
            </div>
            <div className="prepare-check-action">
              {row.selection}
              {row.action && (
                <button
                  type="button"
                  className={`prepare-action${row.action.primary ? ' prepare-action-primary' : ''}`}
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
