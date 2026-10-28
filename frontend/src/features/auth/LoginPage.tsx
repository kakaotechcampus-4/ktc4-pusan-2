import { useEffect, useRef, useState } from 'react';
import { Link, Navigate, useSearchParams } from 'react-router';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';
import { safeDestination, useSession } from './useSession';

/** P11: 로그인 시작과 콜백 상태 안내, 세션 확인 후 목적지 이동을 담당한다. */
function authErrorMessage(authError: string | null): string | null {
  if (authError === null) return null;
  if (authError === 'access_denied') {
    return '로그인이 취소되었습니다. 원하시면 다시 로그인할 수 있습니다.';
  }
  return '로그인을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.';
}

function loginStatusMessage(state: {
  checking: boolean;
  checkFailed: boolean;
  redirecting: boolean;
  fallback: string | null;
}): string | null {
  if (state.checking) return '로그인 상태를 확인하고 있습니다.';
  if (state.checkFailed) {
    return '로그인 상태를 확인하지 못했습니다. 연결을 확인하고 다시 시도해 주세요.';
  }
  if (state.redirecting) return 'Google 로그인 화면으로 이동 중입니다.';
  return state.fallback;
}

export function LoginPage() {
  const [params] = useSearchParams();
  const session = useSession();
  const destination = safeDestination(params.get('next'));
  const [redirecting, setRedirecting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const navigationStarted = useRef(false);

  const authError = params.get('auth_error');
  const error = authErrorMessage(authError);

  useEffect(() => {
    const reset = () => {
      navigationStarted.current = false;
      setRedirecting(false);
    };

    window.addEventListener('pageshow', reset);

    return () => window.removeEventListener('pageshow', reset);
  }, []);

  function login() {
    if (navigationStarted.current) return;

    if (import.meta.env.DEV && import.meta.env.VITE_USE_MOCK !== 'false') {
      setNotice(
        '현재는 화면 미리보기 환경입니다. 실제 Google 로그인은 서버 연결 후 사용할 수 있습니다.',
      );
      return;
    }

    navigationStarted.current = true;
    setNotice(null);
    setRedirecting(true);

    const base = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');
    const callback = `/login?next=${encodeURIComponent(destination)}`;
    window.location.assign(
      `${base}/api/auth/google/start?return_to=${encodeURIComponent(callback)}`,
    );
  }

  const message = loginStatusMessage({
    checking: session.isPending,
    checkFailed: session.isError,
    redirecting,
    fallback: notice || error,
  });

  if (session.data && !authError) return <Navigate replace to={destination} />;

  // 웰컴(`/welcome`)과 같은 머리 · 테두리 · 그림자를 씁니다. 웰컴의 "시작하기"가 이 화면으로 옵니다
  return (
    <div className="flex min-h-dvh flex-col bg-panel text-ink">
      <header className="border-b border-line-strong">
        <div className="mx-auto flex max-w-page flex-wrap items-center justify-between gap-3 px-6 py-4 lg:px-10">
          <Link to="/welcome" aria-label="PITCH COACH · 서비스 소개로">
            <PitchCoachWordmark />
          </Link>
          <Link to="/welcome" className="text-sm font-bold hover:text-ink">
            ← 서비스 소개
          </Link>
        </div>
      </header>

      <main className="flex flex-1 items-center justify-center px-6 py-12">
        <section
          aria-labelledby="login-title"
          className="w-full max-w-md border-2 border-ink bg-panel px-4 py-10 text-center shadow-[4px_4px_0_var(--color-ink)] [overflow-wrap:anywhere] sm:px-12"
        >
          <img
            src="/onboarding/chichi-bust.png"
            alt=""
            width={80}
            height={96}
            className="mx-auto h-24 w-20 object-contain [image-rendering:pixelated]"
          />
          <p className="mt-4 font-mono text-xs tracking-widest text-ink/80">
            READY FOR YOUR FIRST TAKE?
          </p>
          <h1 id="login-title" className="mt-2 text-2xl font-extrabold tracking-tight">
            로그인
          </h1>
          <p className="mt-3 text-sm leading-6 text-ink">
            Google 계정으로 로그인하고
            <br />
            발표 연습을 시작하세요.
          </p>
          <button
            type="button"
            onClick={login}
            disabled={redirecting || session.isPending}
            aria-label="Google 계정으로 로그인"
            aria-describedby="login-status"
            aria-busy={redirecting}
            className="mx-auto mt-8 flex min-h-11 max-w-full cursor-pointer items-center rounded disabled:cursor-wait"
          >
            <img
              src="/auth/google-sign-in.png"
              alt="Google 계정으로 로그인"
              className="h-10 max-w-full w-auto"
            />
          </button>
          <div
            id="login-status"
            role="status"
            aria-live="polite"
            aria-atomic="true"
            className="mt-6 text-sm leading-6 text-ink"
          >
            {message && <p className="border border-line-strong bg-cream px-4 py-3">{message}</p>}
            {session.isError && (
              <button
                className="mt-3 min-h-11 underline"
                onClick={() => session.refetch().catch(() => undefined)}
              >
                연결 다시 확인
              </button>
            )}
          </div>
          {/* 정책 문서가 아직 검토용 초안이라 "동의한 것으로 본다"고 쓰지 않습니다 — 확인만 안내합니다 */}
          <p className="mt-6 border-t border-line pt-5 text-xs leading-5 text-ink/80">
            로그인 전에{' '}
            <Link className="font-bold text-ink underline underline-offset-4" to="/terms">
              이용약관
            </Link>
            과{' '}
            <Link className="font-bold text-ink underline underline-offset-4" to="/privacy">
              개인정보 처리방침
            </Link>
            을 확인해 주세요.
          </p>
        </section>
      </main>

      <footer className="border-t border-line-strong px-6 py-6">
        <p className="mx-auto max-w-page font-mono text-xs font-bold">
          PITCH COACH / ONE TAKE AT A TIME.
        </p>
      </footer>
    </div>
  );
}
