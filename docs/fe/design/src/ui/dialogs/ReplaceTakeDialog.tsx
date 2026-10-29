// 제품 `frontend/src/features/rehearsal/prepare/ReplaceTakeDialog.tsx`에서 복사해 시작했다. 설계서가 관리한다.
import { useEffect, useId, useRef } from 'react';
import { Button } from '../Button';

/**
 * 다른 창 · 기기에서 발표 중인 Take를 끝내고 여기서 시작할지 묻는 확인 창.
 * 끝내는 동안(`busy`)에는 버튼도 Esc도 막는다 — 반쯤 끝낸 채 취소되면 어느 쪽도 남지 않는다.
 * 되돌리기 어려운 쪽이 확인 버튼이라, 처음 초점은 취소에 둔다.
 */
export function ReplaceTakeDialog({
  pitchTitle,
  takeNumber,
  busy,
  onConfirm,
  onCancel,
}: {
  pitchTitle: string;
  takeNumber: number;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const bodyId = useId();
  const mounted = useRef(true);

  useEffect(() => {
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
      aria-describedby={bodyId}
      onCancel={(e) => {
        // Esc다. 닫는 것은 부모가 정한다
        e.preventDefault();
        if (!busy) onCancel();
      }}
      onClose={() => {
        // 브라우저가 Esc를 연달아 받으면 preventDefault를 무시하고 닫는다. 끝내는 중이면 다시 띄운다
        if (!mounted.current || ref.current?.open) return;
        if (busy) ref.current?.showModal();
        else onCancel();
      }}
      className="pitch-create fixed inset-0 m-auto w-[calc(100%-2rem)] max-w-md border-2 border-ink bg-panel p-0 text-ink backdrop:bg-ink/20"
    >
      <div className="px-6 py-6">
        <h2 id={titleId} className="text-lg font-bold">
          다른 곳에서 연습 중이에요
        </h2>
        <p id={bodyId} className="mt-3 text-sm leading-7">
          다른 기기나 창에서 &lsquo;{pitchTitle}&rsquo; {takeNumber}번째 연습이 진행 중이에요. 그
          연습을 끝내고 여기서 새로 시작할까요? 끝낸 연습은 리포트가 만들어지지 않아요.
        </p>
        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <Button variant="secondary" autoFocus disabled={busy} onClick={onCancel}>
            취소
          </Button>
          <Button disabled={busy} onClick={onConfirm}>
            {busy ? '끝내는 중…' : '그 연습 끝내고 시작'}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
