import { useEffect, useId, useRef } from 'react';
import { Button } from '@/shared/ui/Button';
import type { ActiveTake } from '@/types/take';

/**
 * 다른 창 · 기기에서 발표 중인 Take 를 끝내고 여기서 시작할지 묻습니다 (#719).
 * 끝내는 동안(`busy`)에는 버튼도 Esc 도 막습니다 — 반쯤 끝낸 채 취소되면 어느 쪽도 남지 않습니다
 */
export function ReplaceTakeDialog({
  take,
  busy,
  onConfirm,
  onCancel,
}: {
  take: ActiveTake;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const bodyId = useId();
  /** 부모가 이 창을 내리는 중이면 닫힘은 우리가 낸 것입니다. 아래 onClose 가 되살리지 않습니다 */
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
        // Esc 입니다. 닫는 것은 부모가 정합니다 (취소면 이 창을 내립니다)
        e.preventDefault();
        if (!busy) onCancel();
      }}
      onClose={() => {
        // 브라우저가 Esc 를 연달아 받으면 preventDefault 를 무시하고 닫습니다 (사이에 다른 조작이
        // 없을 때). 끝내는 중이면 결과를 알려야 하니 다시 띄우고, 아니면 취소로 봅니다
        if (!mounted.current) return;
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
          다른 기기나 창에서 &lsquo;{take.pitch_title}&rsquo; {take.take_number}번째 연습이 진행
          중이에요. 그 연습을 끝내고 여기서 새로 시작할까요? 끝낸 연습은 리포트가 만들어지지 않아요.
        </p>
        <div className="mt-6 flex flex-wrap justify-end gap-3">
          {/* 다른 곳의 연습을 끝내는 쪽이라, 처음 초점은 되돌릴 수 있는 취소에 둡니다 */}
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
