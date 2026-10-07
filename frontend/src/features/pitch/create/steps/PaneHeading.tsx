import type { ReactNode } from 'react';

/** 본문 머리 — 목업의 큰 제목과 한 줄 설명. 오른쪽에 버튼을 둘 수 있습니다 */
export function PaneHeading({
  title,
  subtitle,
  badges,
  actions,
}: {
  title: string;
  subtitle?: string;
  badges?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-3xl font-bold">{title}</h2>
          {badges}
        </div>
        {subtitle && <p className="mt-1.5 text-base text-stone">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-3">{actions}</div>}
    </div>
  );
}
