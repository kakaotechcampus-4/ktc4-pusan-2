import type { ReactNode } from 'react';

/**
 * 장치 점검 · 리허설 준비가 같이 쓰는 껍데기.
 *
 * 두 화면은 어두운 지면입니다 — 바로 다음이 리허설 무대라서, 여기서 밝았다가
 * 무대에서 어두워지면 발표 직전에 눈이 한 번 적응해야 합니다.
 * 색은 `index.css`의 무대 토큰을 그대로 씁니다. 새 색을 만들지 않습니다.
 *
 * 무대(`stage.css`)와 달리 여기는 평범한 카드·폼이라 Tailwind로 갑니다 (CLAUDE.md 7번).
 */
export function ScreenFrame({
  screenNo,
  screenName,
  entry,
  title,
  subtitle,
  badge,
  onBack,
  hint,
  actions,
  children,
}: {
  screenNo: string;
  screenName: string;
  /** 이 화면으로 들어오는 길 — 시안의 머리말입니다 */
  entry: string;
  title: string;
  subtitle: string;
  badge: string;
  onBack: () => void;
  hint: ReactNode;
  actions: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="min-h-full bg-greige px-4 py-5">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-3">
        <div className="flex flex-wrap items-baseline gap-3 px-1">
          <span className="tabular font-mono text-xs font-bold text-stone">{screenNo}</span>
          <h1 className="text-lg font-bold">{screenName}</h1>
          <span className="text-xs text-stone">{entry}</span>
        </div>

        <div className="overflow-hidden rounded-2xl bg-stage text-ink-stage shadow-lg">
          <header className="flex flex-wrap items-center gap-2 border-b border-stage-panel px-6 py-4">
            <button
              type="button"
              onClick={onBack}
              className="rounded-full border border-stage-panel px-2.5 py-1 text-sm
                         hover:bg-stage-panel"
              aria-label="뒤로"
            >
              ←
            </button>
            <span className="text-sm">뒤로 · {title}</span>
            <span className="text-sm text-stone">· {subtitle}</span>
            <span className="tabular ml-auto font-mono text-xs tracking-wide text-stone">
              {badge}
            </span>
          </header>

          <div className="px-6 py-5">{children}</div>

          <footer className="flex flex-wrap items-center gap-3 border-t border-stage-panel px-6 py-4">
            <p className="text-sm text-stone">{hint}</p>
            <div className="ml-auto flex flex-wrap gap-3">{actions}</div>
          </footer>
        </div>
      </div>
    </div>
  );
}

/** 무대 지면 위의 버튼. 강조 하나(코랄) · 보조 하나(테두리)뿐입니다 */
export function StageButton({
  variant = 'ghost',
  disabled = false,
  onClick,
  children,
}: {
  variant?: 'primary' | 'ghost';
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  const base =
    'rounded-full px-5 py-2.5 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40';
  const skin =
    variant === 'primary'
      ? 'bg-coral text-white hover:bg-coral-deep'
      : 'border border-line-strong text-ink-stage hover:bg-stage-panel';
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={`${base} ${skin}`}>
      {children}
    </button>
  );
}
