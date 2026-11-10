import { useId, useLayoutEffect, useRef } from 'react';
import { Button } from '@/shared/ui/Button';

/** 안내를 띄운 이유. 휴대폰 · 태블릿은 연습 화면 자체가 막혀서(`app/DesktopGate`) 다른 말을 합니다 */
export type ChromeNoticeReason = 'BROWSER' | 'MOBILE';

const BODY: Record<ChromeNoticeReason, string> = {
  BROWSER:
    '피치코치의 시선 분석은 PC의 Google Chrome에서만 동작해요. 지금 브라우저에서는 장치 점검과 연습에서 시선을 재지 못할 수 있어요.',
  MOBILE:
    '발표 연습은 PC의 Google Chrome에서 할 수 있어요. 휴대폰과 태블릿에서는 장치 점검과 연습 화면이 열리지 않아요.',
};

/**
 * PC 의 Chrome 이 아니면 로그인 전에 알리는 창 (#1198). 막지는 않습니다 — 닫으면 그대로 로그인합니다.
 *
 * 로그인 세션은 브라우저마다 따로라서 새 브라우저로 들어온 사용자는 모두 로그인 화면을 거칩니다.
 * 닫은 기록은 남기지 않아 로그인 화면이 열릴 때마다 뜹니다.
 */
export function ChromeNoticeDialog({
  reason,
  onClose,
}: {
  reason: ChromeNoticeReason;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const bodyId = useId();
  const alive = useRef(true);

  // 다른 창(#771)과 같이 DOM 에서 빠지기 전에 닫습니다.
  // React 의 autoFocus 는 창을 열기 전에 실행돼 들어가지 않으므로, 연 뒤에 확인 버튼으로 옮깁니다
  useLayoutEffect(() => {
    alive.current = true;
    const dialog = ref.current;
    dialog?.showModal();
    confirmRef.current?.focus();
    return () => {
      alive.current = false;
      dialog?.close();
    };
  }, []);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      onClose={() => {
        // StrictMode 가 다시 연 창이면 앞서 닫힌 알림을 무시합니다
        if (alive.current && !ref.current?.open) onClose();
      }}
      className="pitch-create fixed inset-0 m-auto max-h-[calc(100dvh_-_2rem)] w-[calc(100%_-_2rem)] max-w-md overflow-y-auto border-2 border-ink bg-panel p-6 text-ink backdrop:bg-ink/40 sm:p-8"
    >
      <h2 id={titleId} className="text-xl font-bold leading-snug">
        PC의 Chrome에서 열어 주세요
      </h2>
      <div id={bodyId} className="mt-3 space-y-2 text-sm leading-6">
        <p>{BODY[reason]}</p>
        <p className="text-ink/80">피치 관리와 리포트는 지금 환경에서도 볼 수 있어요.</p>
      </div>
      <div className="mt-6 flex justify-end">
        <Button ref={confirmRef} onClick={() => ref.current?.close()}>
          확인
        </Button>
      </div>
    </dialog>
  );
}
