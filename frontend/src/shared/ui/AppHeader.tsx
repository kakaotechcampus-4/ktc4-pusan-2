import { useState } from 'react';
import { Link, useLocation } from 'react-router';
import { HomeHelpDialog } from './HomeHelpDialog';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';
import { useLogout } from '@/shared/api/useLogout';

function MenuLink({ to, label, current }: { to: string; label: string; current: boolean }) {
  return (
    <Link
      to={to}
      aria-current={current ? 'page' : undefined}
      className={
        current
          ? 'border-b-2 border-coral py-2 font-bold'
          : 'border-b-2 border-transparent py-2 hover:text-coral-deep'
      }
    >
      {label}
    </Link>
  );
}

/**
 * 홈 · Take 기록 · 마이페이지가 같이 쓰는 상단바. 메뉴를 옮겨도 모양이 바뀌지 않게 한 곳에 둡니다.
 * 바탕은 기본 지면(`bg-panel`)으로 고정합니다 — 아래 화면이 흰 지면이어도 상단바 색은 그대로입니다.
 */
export function AppHeader() {
  const [helpOpen, setHelpOpen] = useState(false);
  const logout = useLogout();
  const { pathname } = useLocation();
  return (
    <header className="border-b border-line-strong bg-panel text-ink">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-5 px-6 py-5 lg:px-10">
        <Link to="/" aria-label="PITCH COACH 홈">
          <PitchCoachWordmark />
        </Link>
        <nav aria-label="홈 메뉴" className="flex items-center gap-5 text-xs sm:gap-8 sm:text-sm">
          <MenuLink to="/" label="내 피치" current={pathname === '/' || pathname === '/pitches'} />
          <MenuLink to="/takes" label="Take 기록" current={pathname === '/takes'} />
          <button type="button" onClick={() => setHelpOpen(true)} className="hover:text-coral-deep">
            사용방법
          </button>
          <MenuLink to="/me" label="마이페이지" current={pathname === '/me'} />
          <button
            disabled={logout.isPending}
            onClick={() => logout.mutate()}
            className="text-stone hover:text-ink disabled:opacity-50"
          >
            {logout.isPending ? '로그아웃 중…' : '로그아웃'}
          </button>
        </nav>
      </div>
      {logout.isError && (
        <p role="alert" className="px-6 pb-3 text-center text-sm text-coral-deep">
          로그아웃하지 못했어요. 다시 시도해 주세요.
        </p>
      )}
      {helpOpen && <HomeHelpDialog onDismiss={() => setHelpOpen(false)} />}
    </header>
  );
}
