import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';

/**
 * 글 위주 화면의 틀 — 분석 방식 설명(P14) · 미지원 브라우저(P18) · 404 · 마이페이지(P9).
 * 홈으로 가는 로고 줄을 직접 둡니다. 로그인 없이 열리는 화면에는 앱 머리(RequireSession)가 없어서입니다.
 * 상단바(`AppHeader`) 아래에 놓이는 Take 기록 · 마이페이지는 `logo={false}` 로 로고를 빼고 제목만 둡니다.
 * 모양은 디자인 시안(`dev/design/ui.tsx` DraftPage)과 같습니다.
 */
export function InfoPage({
  title,
  logo = true,
  children,
}: {
  title: string;
  logo?: boolean;
  children: ReactNode;
}) {
  return (
    // 밝은 지면(시안 10–12 기준). 토큰만 바꾸는 클래스입니다 (index.css)
    <div className="pitch-create min-h-dvh bg-greige text-ink">
      <header className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-3 px-5 pt-6 sm:px-8">
        {logo && (
          <>
            <Link to="/" aria-label="피치코치 홈">
              <PitchCoachWordmark />
            </Link>
            <span className="h-5 w-px bg-line-strong" aria-hidden="true" />
          </>
        )}
        <h1 className="text-lg font-bold">{title}</h1>
      </header>

      <main className="mx-auto flex max-w-7xl flex-col gap-6 px-5 pb-12 pt-5 sm:px-8">
        {children}
      </main>
    </div>
  );
}
