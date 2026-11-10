// 제품 `frontend/src/features/auth/PrivacyConsentDialog.tsx`에서 복사해 시작했다. 설계서가 관리한다.
import { useEffect, useId, useRef } from 'react';
import { Button } from '../Button';

/** 제품 PrivacyConsentDialog 의 안내 항목. 문구는 제품과 같다 */
const DATA_USES = [
  [
    '얼굴·시선',
    '카메라 영상은 기기 안에서만 분석하며 서버로 전송하거나 저장하지 않습니다. 시선 분석 결과(시선 구간)만 연습 기록으로 저장합니다.',
  ],
  ['음성', 'Deepgram STT로 텍스트 변환하며, 서비스 서버에 녹음 파일은 저장하지 않습니다.'],
  [
    '분석 정보',
    '발표 분석 결과, 변환된 텍스트, 오류 로그 등은 기능 검증 및 개선을 위해 2026년 12월 31일까지 보관합니다.',
  ],
] as const;

/**
 * 로그인한 뒤 동의 전이면 화면 위에 띄우는 동의 창. 동의해야만 닫힌다 — Esc · 닫기 버튼이 없다.
 * 로그아웃 중에는 `aria-disabled` 로 막는다(`disabled` 면 초점이 창 밖으로 빠진다, #845).
 */
export function PrivacyConsentDialog({
  signingOut = false,
  signOutFailed = false,
  onAgree,
  onSignOut,
  onOpenPolicy,
}: {
  signingOut?: boolean;
  signOutFailed?: boolean;
  onAgree: () => void;
  onSignOut: () => void;
  onOpenPolicy: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const titleId = useId();
  const bodyId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (typeof dialog?.showModal === 'function' && !dialog.open) dialog.showModal();
    headingRef.current?.focus();
    return () => dialog?.close?.();
  }, []);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      onCancel={(event) => event.preventDefault()}
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
          <button
            type="button"
            onClick={onOpenPolicy}
            className="font-bold underline underline-offset-4"
          >
            개인정보 처리방침 ↗
          </button>
        </p>

        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Button onClick={onAgree}>동의하고 시작하기</Button>
          <Button
            variant="quiet"
            aria-disabled={signingOut || undefined}
            onClick={() => {
              if (!signingOut) onSignOut();
            }}
          >
            {signingOut ? '로그아웃 중…' : '동의하지 않고 로그아웃'}
          </Button>
        </div>
        {signOutFailed && (
          <p role="alert" className="mt-3 text-sm">
            로그아웃에 실패했습니다. 다시 시도해 주세요.
          </p>
        )}
      </div>
    </dialog>
  );
}
