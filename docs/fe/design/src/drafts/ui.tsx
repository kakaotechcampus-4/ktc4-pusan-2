import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { PitchCoachWordmark } from '@/ui/PitchCoachWordmark';

/**
 * 디자인 시안 공통 틀 · 조각. 페이지 틀 · 마스코트만 둡니다. 버튼 · 카드 · 배지는 설계서의 `@/ui/*` 에서 가져옵니다.
 * 스타일은 리포트 화면(`features/report/ReportPage.tsx`)과 같습니다: 진한 테두리 · 각진 모서리 · 밀린 그림자.
 */

export interface DraftVariant<V extends string> {
  id: V;
  label: string;
}

export function DraftPage<V extends string>({
  title,
  eyebrow,
  meta,
  variants,
  variant,
  onVariant,
  questions,
  fitViewport = false,
  children,
}: {
  title: string;
  /** 제목 아래 줄 왼쪽. 피치 이름 · Take 번호 같은 맥락 */
  eyebrow?: ReactNode;
  /** 머리 오른쪽 작은 글씨 */
  meta?: ReactNode;
  variants?: DraftVariant<V>[];
  variant?: V;
  onVariant?: (v: V) => void;
  /** 시안에서 아직 정하지 못한 것 */
  questions: string[];
  /** 화면 틀을 한 뷰포트에 맞추고 긴 내용만 안쪽에서 스크롤합니다 */
  fitViewport?: boolean;
  children: ReactNode;
}) {
  return (
    // 밝은 지면(시안 10–12 기준). 토큰만 바꾸는 클래스입니다 (index.css)
    <div
      className={`pitch-create bg-greige text-ink ${fitViewport ? 'flex h-dvh flex-col overflow-hidden' : 'min-h-dvh'}`}
    >
      <header
        className={`mx-auto flex w-full max-w-7xl shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-3 px-5 sm:px-8 ${fitViewport ? 'pt-4' : 'pt-6'}`}
      >
        <div className="flex flex-wrap items-center gap-4">
          <Link to="/" aria-label="시안 목록">
            <PitchCoachWordmark />
          </Link>
          <span className="h-5 w-px bg-line-strong" aria-hidden="true" />
          <h1 className="text-lg font-bold">{title}</h1>
        </div>
        {meta && <p className="tabular text-sm text-stone">{meta}</p>}
      </header>

      <main
        className={`mx-auto flex w-full max-w-7xl flex-col px-5 sm:px-8 ${fitViewport ? 'min-h-0 flex-1 gap-2 overflow-hidden pb-3 pt-2' : 'gap-6 pb-12 pt-5'}`}
      >
        <div
          className={`flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-line ${fitViewport ? 'pb-2' : 'pb-4'}`}
        >
          <div className="font-bold">{eyebrow}</div>
          <span className="font-mono text-xs tracking-widest text-stone">DESIGN DRAFT · 시안</span>
        </div>

        {children}

        <details
          className={`shrink-0 border-t border-line pt-3 text-sm ${fitViewport ? 'max-h-[35vh] overflow-y-auto' : ''}`}
          open={!fitViewport}
        >
          <summary className="w-fit cursor-pointer font-bold text-stone">시안 검토 도구</summary>
          <div className="mt-4 space-y-4">
            {variants && variant && onVariant && (
              <div className="flex flex-wrap items-center gap-2 border border-dashed border-line-strong px-4 py-2.5">
                <span className="mr-2 text-xs font-bold text-stone">시안 상태</span>
                {variants.map((v) => (
                  <button
                    key={v.id}
                    type="button"
                    aria-pressed={variant === v.id}
                    onClick={() => onVariant(v.id)}
                    className={[
                      'px-3 py-1 text-sm font-bold',
                      variant === v.id ? 'bg-ink text-panel' : 'bg-panel text-stone hover:text-ink',
                    ].join(' ')}
                  >
                    {v.label}
                  </button>
                ))}
              </div>
            )}
            <aside className="border border-dashed border-line-strong px-5 py-4">
              <p className="font-bold">시안에서 정해야 할 것</p>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-stone">
                {questions.map((q) => (
                  <li key={q}>{q}</li>
                ))}
              </ul>
            </aside>
          </div>
        </details>
      </main>
    </div>
  );
}

export { Mascot } from '@/ui/Mascot';
