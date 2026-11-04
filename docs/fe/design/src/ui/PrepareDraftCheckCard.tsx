// PM 검토용 독립 시안. 닫힌 #789의 표시 코드를 가져왔으며 제품 모듈·API·장비와 연결하지 않는다.
import type { ReactNode } from 'react';

/** 상태 문장과 장치 선택/버튼을 분리해, 줄 전체를 누르는 동작을 없앱니다. */
export function PrepareDraftCheckCard({
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
    <section className="prepare-draft-equipment" aria-label={title}>
      <div className="prepare-draft-section-heading">
        <h2>{title}</h2>
        <span className="prepare-draft-required-status">
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
      <ul className="prepare-draft-checks">
        {rows.map((row) => (
          <li key={row.id} className="prepare-draft-check" data-done={row.done}>
            <span className="prepare-draft-check-name">
              {row.name}
              {row.optional && <small>선택</small>}
            </span>
            <div className="prepare-draft-check-status">
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
              {row.trailing && <div className="prepare-draft-check-meter">{row.trailing}</div>}
            </div>
            <div className="prepare-draft-check-action">
              {row.selection}
              {row.action && (
                <button
                  type="button"
                  className={`prepare-draft-action${row.action.primary ? ' prepare-draft-action-primary' : ''}`}
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
