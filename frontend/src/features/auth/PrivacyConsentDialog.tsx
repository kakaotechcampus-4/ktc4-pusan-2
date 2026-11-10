import { useId, useLayoutEffect, useRef } from 'react';
import { Link } from 'react-router';
import { Button } from '@/shared/ui/Button';
import { recordPrivacyConsent } from '@/shared/lib/privacyConsent';
import { DATA_RETENTION_UNTIL } from '@/shared/lib/retention';
import { useLogout } from './useSession';

/** 안내문을 고치면 `PRIVACY_CONSENT_VERSION`(privacyConsent.ts)을 올립니다 */
const DATA_USES = [
  [
    '얼굴·시선',
    '카메라 영상은 기기 안에서만 분석하며 서버로 전송하거나 저장하지 않습니다. 시선 분석 결과(시선 구간)만 연습 기록으로 저장합니다.',
  ],
  ['음성', 'Deepgram STT로 텍스트 변환하며, 서비스 서버에 녹음 파일은 저장하지 않습니다.'],
  [
    '분석 정보',
    `발표 분석 결과, 변환된 텍스트, 오류 로그 등은 기능 검증 및 개선을 위해 ${DATA_RETENTION_UNTIL}까지 보관합니다.`,
  ],
] as const;

const OPENED_SCREEN_WAIT_MS = 5000;

/**
 * 동의하면 창이 닫히고 초점이 body 로 빠집니다. 뒤에 있던 화면의 첫 제목(h1)으로 초점을 옮겨,
 * 화면낭독기가 무엇이 열렸는지 읽게 합니다 (#845 와 같은 이유).
 * 화면을 늦게 그리면(lazy · 연습 화면) 제목이 생길 때까지 기다리고, 그사이 사용자가 다른 곳으로 초점을 옮기면 건드리지 않습니다.
 */
function focusOpenedScreen() {
  const tryFocus = () => {
    const active = document.activeElement;
    if (active && active !== document.body) return true;
    const heading = document.querySelector<HTMLElement>('h1');
    if (!heading) return false;
    if (!heading.hasAttribute('tabindex')) heading.tabIndex = -1;
    heading.focus();
    return true;
  };
  const observer = new MutationObserver(() => {
    if (tryFocus()) stop();
  });
  const timer = window.setTimeout(stop, OPENED_SCREEN_WAIT_MS);
  function stop() {
    observer.disconnect();
    window.clearTimeout(timer);
  }
  // 창이 닫힌 뒤(React 가 다시 그린 뒤)에 봅니다
  window.setTimeout(() => {
    if (tryFocus()) stop();
    else observer.observe(document.body, { childList: true, subtree: true });
  }, 0);
}

/**
 * 로그인한 사용자가 서비스를 쓰기 전에 한 번 보는 동의 창입니다. `RequireSession` 이 동의 전에는 화면 위에 이것을 띄웁니다.
 * 동의해야만 닫힙니다 — Esc · 닫기 버튼이 없고, 뒤 화면은 모달이라 누를 수 없습니다. 주소는 옮기지 않습니다.
 * 로그아웃 버튼은 `disabled` 대신 `aria-disabled` 로 막습니다. `disabled` 면 누른 버튼의 초점이 창 밖으로 빠집니다 (#845).
 */
export function PrivacyConsentDialog({ userId }: { userId: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const titleId = useId();
  const bodyId = useId();
  const alive = useRef(true);
  const signOut = useLogout();

  // 닫기를 DOM 에서 빠지기 전에 합니다 (#766 · #771)
  useLayoutEffect(() => {
    alive.current = true;
    const dialog = ref.current;
    dialog?.showModal();
    // 안내문부터 읽히도록 첫 초점은 제목에 둡니다(첫 링크 · 버튼이 아니라)
    headingRef.current?.focus();
    return () => {
      alive.current = false;
      dialog?.close();
    };
  }, []);

  const agree = () => {
    recordPrivacyConsent(userId);
    focusOpenedScreen();
  };

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      onCancel={(event) => event.preventDefault()}
      onClose={() => {
        // Esc 를 연달아 받은 브라우저가 강제로 닫았으면 다시 띄웁니다 — 동의 전에는 닫히지 않습니다
        if (alive.current && !ref.current?.open) ref.current?.showModal();
      }}
      className="fixed inset-0 m-auto max-h-[calc(100dvh_-_2rem)] w-[calc(100%_-_2rem)] max-w-xl overflow-y-auto border-2 border-ink bg-panel p-0 text-ink shadow-[4px_4px_0_var(--color-ink)] [overflow-wrap:anywhere] backdrop:bg-ink/40"
    >
      <div className="px-4 py-8 sm:px-10">
        <h2
          ref={headingRef}
          id={titleId}
          tabIndex={-1}
          className="text-2xl font-extrabold tracking-tight outline-none"
        >
          개인정보 처리 및 데이터 활용
        </h2>

        <div id={bodyId}>
          <dl className="mt-6 flex flex-col gap-4 text-sm leading-6">
            {DATA_USES.map(([label, text]) => (
              <div key={label} className="border-l-2 border-ink pl-3">
                <dt className="font-bold">{label}</dt>
                <dd>{text}</dd>
              </div>
            ))}
          </dl>

          <p className="mt-6 border border-line-strong bg-cream px-4 py-3 text-sm leading-6">
            현재 사용자 테스트 단계로, 오류 확인과 기능 개선을 위한 최소한의 분석 정보 수집이
            필수입니다. 관련 내용을 안내하고 동의를 하신 후 서비스를 이용할 수 있습니다.
          </p>
        </div>

        <p className="mt-4 text-sm">
          <Link
            to="/privacy"
            target="_blank"
            rel="noreferrer"
            className="font-bold underline underline-offset-4"
          >
            개인정보 처리방침 ↗
          </Link>
        </p>

        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Button onClick={agree}>동의하고 시작하기</Button>
          <Button
            variant="quiet"
            aria-disabled={signOut.isPending || undefined}
            onClick={() => {
              if (!signOut.isPending) signOut.mutate();
            }}
          >
            {signOut.isPending ? '로그아웃 중…' : '동의하지 않고 로그아웃'}
          </Button>
        </div>
        {signOut.isError && (
          <p role="alert" className="mt-3 text-sm">
            로그아웃에 실패했습니다. 다시 시도해 주세요.
          </p>
        )}
      </div>
    </dialog>
  );
}
