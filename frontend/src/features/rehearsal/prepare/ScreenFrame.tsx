import type { ReactNode } from 'react';
import './prepare.css';

export function ScreenFrame({
  title,
  subtitle,
  badge,
  onBack,
  hint,
  actions,
  children,
}: {
  title: string;
  subtitle: string;
  badge: string;
  onBack: () => void;
  hint: ReactNode;
  actions: ReactNode;
  children: ReactNode;
}) {
  return (
    <main className="pitch-create prepare-screen">
      <div className="prepare-frame">
        <header className="prepare-heading">
          <button type="button" onClick={onBack} className="prepare-action" aria-label="뒤로">
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              aria-hidden="true"
            >
              <path d="m12 5-7 7 7 7M5 12h15" />
            </svg>
          </button>
          <h1>시작 전 세팅</h1>
          <div className="prepare-pitch">
            <p title={title}>{title}</p>
            <p>{subtitle}</p>
          </div>
          <span className="prepare-take">{badge}</span>
        </header>
        {children}
        <footer className="prepare-footer">
          <p role="status">{hint}</p>
          <div className="prepare-start">{actions}</div>
        </footer>
      </div>
    </main>
  );
}
