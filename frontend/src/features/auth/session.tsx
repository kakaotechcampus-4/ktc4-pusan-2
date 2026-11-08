import type { ReactNode } from 'react';
import { Navigate, NavLink, Outlet, useLocation } from 'react-router';
import { useLogout, useSession } from './useSession';

/**
 * @param stageBar 리허설 무대 위에 이름 · 로그아웃 한 줄을 얹고 무대가 남은 높이를 받게 합니다
 * @param header 주면 이것을 얹습니다 (홈 · 피치 생성 · 시작 전 세팅 등의 상단바)
 */
export function RequireSession({
  stageBar = false,
  header,
}: {
  stageBar?: boolean;
  header?: ReactNode;
}) {
  const session = useSession();
  const signOut = useLogout();
  const location = useLocation();

  const oauthError = new URLSearchParams(location.search).get('auth_error');
  if (oauthError)
    return <Navigate replace to={`/login?auth_error=${encodeURIComponent(oauthError)}`} />;

  if (session.isPending)
    return (
      <p role="status" className="p-8">
        로그인 상태를 확인하고 있습니다.
      </p>
    );

  if (session.isError)
    return (
      <div role="alert" className="p-8">
        서버에 연결하지 못했습니다.{' '}
        <button onClick={() => session.refetch().catch(() => undefined)}>다시 시도</button>
      </div>
    );

  if (!session.data)
    return (
      <Navigate
        replace
        to={
          location.pathname === '/'
            ? '/welcome'
            : `/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`
        }
      />
    );

  if (header)
    return (
      // 상단바와 화면이 함께 한 화면 높이를 채웁니다. 화면은 flex-1 로 남은 높이를 받아,
      // 내용이 짧으면 상단바 높이만큼 스크롤이 생기지 않습니다.
      <div className="app-scroll-shell flex h-dvh flex-col overflow-y-auto [scrollbar-gutter:stable]">
        {header}
        <Outlet />
      </div>
    );

  if (!stageBar)
    return (
      <div className="contents">
        <Outlet />
      </div>
    );

  return (
    <div className="rehearsal-shell">
      <div className="flex items-center justify-end gap-4 px-6 py-3 text-sm">
        {/* 헤더 높이로 무대 위치가 달라지지 않도록 한 줄을 유지합니다. */}
        <span title={session.data.name} className="min-w-0 truncate">
          {session.data.name}
        </span>
        <NavLink to="/takes" end className="underline aria-[current=page]:font-bold">
          Take 기록
        </NavLink>
        <NavLink to="/me" className="underline aria-[current=page]:font-bold">
          마이페이지
        </NavLink>
        <button disabled={signOut.isPending} onClick={() => signOut.mutate()} className="underline">
          {signOut.isPending ? '로그아웃 중…' : '로그아웃'}
        </button>
        {signOut.isError && <span role="alert">로그아웃에 실패했습니다. 다시 시도해 주세요.</span>}
      </div>
      <Outlet />
    </div>
  );
}
