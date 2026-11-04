import type { ReactNode } from 'react';

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
    <main className="pitch-create prepare-screen bg-greige px-6 pt-3 pb-4 text-ink prepare-compact:px-5 prepare-compact:py-2 max-[600px]:px-3 selection:bg-coral-wash selection:text-ink [&_:focus-visible]:outline-coral-deep">
      <div className="prepare-frame mx-auto max-w-page">
        <header className="prepare-heading mb-3 flex min-h-14 items-center gap-3.5 prepare-compact:mb-2 prepare-compact:min-h-11 max-lg:flex-wrap [&>h1]:shrink-0 [&>h1]:text-[22px] [&>h1]:font-bold prepare-compact:[&>h1]:text-xl">
          <button
            type="button"
            onClick={onBack}
            className="prepare-action inline-flex min-h-8 shrink-0 items-center justify-center gap-1 border border-ink bg-panel px-2.5 py-1 text-xs leading-[18px] font-semibold text-ink hover:bg-cream disabled:pointer-events-none disabled:cursor-default disabled:border-dashed disabled:border-line-strong disabled:bg-cream disabled:text-ink prepare-low:min-h-7 prepare-low:py-0.5"
            aria-label="뒤로"
          >
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
          <div className="prepare-pitch min-w-0 border-l border-line-strong pl-4 max-lg:order-1 max-lg:basis-full max-lg:border-0 max-lg:p-0 [&>p:first-child]:truncate [&>p:first-child]:text-sm [&>p:first-child]:font-semibold [&>p:last-child]:text-xs [&>p:last-child]:leading-[18px] [&>p:last-child]:wrap-anywhere">
            <p title={title}>{title}</p>
            <p>{subtitle}</p>
          </div>
          <span className="prepare-take ml-auto shrink-0 text-sm font-bold tabular-nums">
            {badge}
          </span>
        </header>
        {children}
        <footer className="prepare-footer mt-4 flex items-center gap-4 border-t border-line-strong pt-3 prepare-compact:mt-2 prepare-compact:pt-2 max-[600px]:flex-col max-[600px]:items-stretch [&>p]:min-w-0 [&>p]:flex-1 [&>p]:text-[13px] [&>p]:leading-5 [&>p]:wrap-anywhere">
          <p role="status">{hint}</p>
          <div className="prepare-start shrink-0 [&>button]:min-h-11 [&>button]:min-w-[180px] [&>button]:border [&>button]:shadow-none max-[600px]:[&>button]:w-full">
            {actions}
          </div>
        </footer>
      </div>
    </main>
  );
}
