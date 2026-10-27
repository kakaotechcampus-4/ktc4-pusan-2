import type { ReactNode } from 'react';
import { Navigate, NavLink, Outlet, useLocation } from 'react-router';
import { useLogout, useSession } from './useSession';

/**
 * @param bright 이름 · 로그아웃 줄을 아래 화면과 같은 밝은 지면(`pitch-create`)으로 둡니다.
 *   리허설 무대는 기본 지면이라 끕니다 — 켜면 무대 위에 밝은 띠가 생깁니다
 * @param header 주면 이름 · 로그아웃 줄 대신 이것을 얹습니다 (홈 · Take 기록 · 마이페이지의 상단바)
 */
export function RequireSession({
  showHeader = true,
  bright = true,
  header,
}: {
  showHeader?: boolean;
  bright?: boolean;
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
      <div className="flex min-h-dvh flex-col">
        {header}
        <Outlet />
      </div>
    );

  return (
    <>
      {showHeader && (
        <div
          className={`${bright ? 'pitch-create flex-wrap bg-greige text-ink [&>a]:shrink-0 [&>button]:shrink-0 ' : ''}flex items-center justify-end gap-4 px-6 py-3 text-sm`}
        >
          {/* 리허설은 헤더 높이로 무대 위치가 달라지지 않도록 한 줄을 유지합니다. */}
          <span
            title={session.data.name}
            className={bright ? 'min-w-0 max-w-full [overflow-wrap:anywhere]' : 'min-w-0 truncate'}
          >
            {session.data.name}
          </span>
          <NavLink to="/takes" end className="underline aria-[current=page]:font-bold">
            Take 기록
          </NavLink>
          <NavLink to="/me" className="underline aria-[current=page]:font-bold">
            마이페이지
          </NavLink>
          <button
            disabled={signOut.isPending}
            onClick={() => signOut.mutate()}
            className="underline disabled:opacity-60"
          >
            {signOut.isPending ? '로그아웃 중…' : '로그아웃'}
          </button>
          {signOut.isError && (
            <span role="alert">로그아웃에 실패했습니다. 다시 시도해 주세요.</span>
          )}
        </div>
      )}
      <Outlet />
    </>
  );
}
