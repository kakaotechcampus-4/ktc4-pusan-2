import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';

/** P11: 화면과 로그인 시작만 담당한다. 세션 복원은 추후 공용 인증 계층에서 연결한다. */
export function LoginPage() {
  const [params] = useSearchParams();
  const [redirecting, setRedirecting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const navigationStarted = useRef(false);
  const authError = params.get('auth_error');
  const error =
    authError === null
      ? null
      : authError === 'access_denied'
        ? 'Google 로그인이 취소되었습니다. 아래 버튼을 눌러 다시 로그인해 주세요.'
        : '로그인을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.';

  useEffect(() => {
    document.title = '로그인 | 피치코치';
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
    window.location.assign(`${base}/api/auth/google/start?return_to=/login`);
  }

  const message = redirecting ? 'Google 로그인 화면으로 이동 중입니다.' : notice || error;

  return (
    <main className="flex min-h-dvh items-center justify-center px-6 py-12">
      <section
        aria-labelledby="login-title"
        className="w-full max-w-md rounded-2xl border border-line bg-panel px-8 py-14 text-center sm:px-12"
      >
        <h1 id="login-title" className="text-2xl font-bold tracking-tight">
          로그인
        </h1>
        <p className="mt-4 text-sm leading-6 text-ink">
          Google 계정으로 로그인하고
          <br />
          발표 연습을 시작하세요.
        </p>
        <button
          type="button"
          onClick={login}
          disabled={redirecting}
          aria-label="Google 계정으로 로그인"
          aria-describedby="login-status"
          aria-busy={redirecting}
          className="mx-auto mt-8 block max-w-full cursor-pointer rounded disabled:cursor-wait disabled:opacity-60"
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
          {message && <p className="rounded-lg border border-line bg-cream px-4 py-3">{message}</p>}
        </div>
      </section>
    </main>
  );
}
