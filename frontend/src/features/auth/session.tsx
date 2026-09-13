import { Navigate, Outlet, useLocation } from 'react-router';
import { useLogout, useSession } from './useSession';
export function RequireSession() {
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
        <button onClick={() => void session.refetch()}>다시 시도</button>
      </div>
    );
  if (!session.data)
    return (
      <Navigate
        replace
        to={`/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`}
      />
    );
  return (
    <>
      <div className="flex items-center justify-end gap-4 px-6 py-3 text-sm">
        <span>{session.data.name}</span>
        <button
          disabled={signOut.isPending}
          onClick={() => signOut.mutate()}
          className="underline disabled:opacity-60"
        >
          {signOut.isPending ? '로그아웃 중…' : '로그아웃'}
        </button>
        {signOut.isError && <span role="alert">로그아웃에 실패했습니다. 다시 시도해 주세요.</span>}
      </div>
      <Outlet />
    </>
  );
}
