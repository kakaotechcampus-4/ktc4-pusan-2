import type { ReactNode } from 'react';

export function Card({
  title,
  action,
  children,
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-4 border-2 border-ink bg-panel p-5 shadow-[4px_4px_0_var(--color-line)] sm:p-6">
      {(title || action) && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          {title && <h2 className="text-lg font-bold">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  children,
}: {
  label: string;
  value: string;
  children?: ReactNode;
}) {
  return (
    <div className="border-2 border-ink bg-panel px-5 py-5 shadow-[4px_4px_0_var(--color-line)]">
      <p className="text-sm font-bold text-stone">{label}</p>
      <p className="tabular mt-4 font-mono text-3xl font-bold sm:text-4xl">{value}</p>
      {children && <p className="tabular mt-3 text-xs text-stone">{children}</p>}
    </div>
  );
}
