// 제품 `frontend/src/features/pitch/DeletePitchDialog.tsx`에서 복사해 시작했다. 설계서가 관리한다.
import { useId, useLayoutEffect, useRef } from 'react';
import { Button } from '../Button';

/** 실패 문구. 제품은 누르는 순간 목록을 다시 받아 연습 · 분석이 시작됐으면 `busy`, 요청이 실패하면 `failed`다 */
export type DeletePitchError = 'failed' | 'busy' | null;

/**
 * 홈에서 피치를 지울지 묻는 확인 창(#807). 영구 삭제라 되돌릴 수 없다는 것과 함께 지워지는 것을 적는다.
 * 처음 초점은 취소. 지우는 동안(`busy`)에는 두 버튼을 aria-disabled로 막고 Esc도 막는다.
 * 실패하면 창을 닫지 않고 이유를 알린다.
 */
export function DeletePitchDialog({
  pitchTitle,
  takeCount,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  pitchTitle: string;
  takeCount: number;
  busy: boolean;
  error: DeletePitchError;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const nameId = useId();
  const bodyId = useId();
  const mounted = useRef(true);

  useLayoutEffect(() => {
    mounted.current = true;
    const dialog = ref.current;
    dialog?.showModal();
    return () => {
      mounted.current = false;
      dialog?.close();
    };
  }, []);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={`${nameId} ${bodyId}`}
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onCancel();
      }}
      onClose={() => {
        if (!mounted.current || ref.current?.open) return;
        if (busy) ref.current?.showModal();
        else onCancel();
      }}
      className="pitch-create fixed inset-0 m-auto w-[calc(100%-2rem)] max-w-md border-2 border-ink bg-panel p-0 text-ink backdrop:bg-ink/20"
    >
      <div className="px-6 py-6">
        <h2 id={titleId} className="text-lg font-bold">
          이 피치를 삭제할까요?
        </h2>
        <p
          id={nameId}
          className="mt-3 border border-line-strong bg-white px-3 py-2 font-bold break-words"
        >
          {pitchTitle}
        </p>
        <p id={bodyId} className="mt-3 text-sm leading-7">
          삭제하면 되돌릴 수 없어요.{' '}
          {takeCount > 0
            ? `이 피치의 Take 기록 ${takeCount}개와 리포트, 발표 자료 · 대본 · 평가 기준이 모두 함께 지워져요.`
            : '이 피치의 발표 자료 · 대본 · 평가 기준이 모두 함께 지워져요.'}
        </p>
        {error && (
          <p role="alert" className="mt-3 text-sm leading-6 font-bold text-coral-deep">
            {error === 'busy'
              ? '지금 이 피치로 연습하거나 분석하는 중이에요. 끝난 뒤 다시 삭제해 주세요.'
              : '피치를 삭제하지 못했어요. 잠시 뒤 다시 시도해 주세요.'}
          </p>
        )}
        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <Button
            variant="secondary"
            autoFocus
            aria-disabled={busy}
            onClick={() => {
              if (!busy) onCancel();
            }}
          >
            취소
          </Button>
          <Button
            aria-disabled={busy}
            onClick={() => {
              if (!busy) onConfirm();
            }}
          >
            {busy ? '삭제하는 중…' : '삭제'}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
