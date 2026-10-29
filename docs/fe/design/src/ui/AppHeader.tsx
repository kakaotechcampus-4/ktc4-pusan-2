// 제품 `frontend/src/shared/ui/AppHeader.tsx`에서 복사해 시작했다. 설계서가 관리한다.
// 로그아웃 · 세션은 없다. 메뉴는 Link라 미리보기 안에서 누르면 설계서 화면으로 간다(ScreenPreview targetFor).
import { useState } from 'react';
import { Link } from 'react-router';
import { HomeHelpDialog } from './dialogs/HomeHelpDialog';
import { PitchCoachWordmark } from './PitchCoachWordmark';

function MenuLink({ to, label, current }: { to: string; label: string; current: boolean }) {
  return (
    <Link
      to={to}
      aria-current={current ? 'page' : undefined}
      className={
        current
          ? 'inline-flex min-h-11 items-center border-b-2 border-coral py-2 font-bold'
          : 'inline-flex min-h-11 items-center border-b-2 border-transparent py-2 hover:text-coral-deep'
      }
    >
      {label}
    </Link>
  );
}

/**
 * 홈 · Take 기록 · 마이페이지가 같이 쓰는 상단바. 바탕은 기본 지면(`bg-panel`)으로 고정한다 —
 * 아래 화면이 흰 지면이어도 상단바 색은 그대로다.
 */
export function AppHeader({ current }: { current: 'home' | 'takes' | 'me' }) {
  const [helpOpen, setHelpOpen] = useState(false);
  return (
    <header className="border-b border-line-strong bg-panel text-ink">
      <div className="mx-auto flex max-w-page flex-wrap items-center justify-between gap-5 px-6 py-5 lg:px-10">
        <Link to="/" aria-label="PITCH COACH 홈">
          <PitchCoachWordmark />
        </Link>
        <nav
          aria-label="홈 메뉴"
          className="flex flex-wrap items-center gap-5 text-xs sm:gap-8 sm:text-sm"
        >
          <MenuLink to="/" label="내 피치" current={current === 'home'} />
          <MenuLink to="/takes" label="Take 기록" current={current === 'takes'} />
          <button
            type="button"
            onClick={() => setHelpOpen(true)}
            className="min-h-11 hover:text-ink"
          >
            사용방법
          </button>
          <MenuLink to="/me" label="마이페이지" current={current === 'me'} />
          <button type="button" className="min-h-11 text-ink/80 hover:text-ink">
            로그아웃
          </button>
        </nav>
      </div>
      {helpOpen && <HomeHelpDialog onDismiss={() => setHelpOpen(false)} />}
    </header>
  );
}
