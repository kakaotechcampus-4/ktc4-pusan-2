// PM 검토용 독립 시안. 닫힌 #789의 표시 코드를 가져왔으며 제품 모듈·API·장비와 연결하지 않는다.
import type { ReactNode } from 'react';
import '../screens/prepare-draft.css';

export function PrepareDraftFrame({
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
    <div className="pitch-create prepare-draft-screen">
      <div className="prepare-draft-frame">
        <header className="prepare-draft-heading">
          <button type="button" onClick={onBack} className="prepare-draft-action" aria-label="뒤로">
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
          <div className="prepare-draft-pitch">
            <p title={title}>{title}</p>
            <p>{subtitle}</p>
          </div>
          <span className="prepare-draft-take">{badge}</span>
        </header>
        {children}
        <footer className="prepare-draft-footer">
          <p role="status">{hint}</p>
          <div className="prepare-draft-start">{actions}</div>
        </footer>
      </div>
    </div>
  );
}
