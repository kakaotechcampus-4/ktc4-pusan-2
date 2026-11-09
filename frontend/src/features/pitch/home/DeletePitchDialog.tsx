import { useId, useLayoutEffect, useRef } from 'react';
import { toMessage } from '@/shared/api/errorMessage';
import { isPitchBusy, useDeletePitch } from '@/shared/api/pitch';
import { Button } from '@/shared/ui/Button';
import type { HomePitch } from '@/types/home';
import { SURVEY_RETENTION_UNTIL } from './HomeSurveyNotice';

/**
 * 홈에서 피치를 지울지 묻습니다 (#807). 영구 삭제라 되돌릴 수 없다는 것과 함께 지워지는 것을 적고,
 * 처음 초점은 취소에 둡니다. 지우는 동안(`busy`)에는 버튼도 Esc 도 막습니다 — 보낸 요청은 거둘 수 없습니다.
 * 버튼은 `disabled` 대신 `aria-disabled` 로 막습니다. `disabled` 면 누른 버튼의 초점이 창 밖으로 빠집니다 (#845).
 * 실패하면 창을 그대로 두고 이유를 알립니다. 초점은 부모가 옮깁니다 (`HomePage`)
 */
export function DeletePitchDialog({
  pitch,
  onCancel,
  onDeleted,
}: {
  pitch: HomePitch;
  onCancel: () => void;
  onDeleted: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const nameId = useId();
  const bodyId = useId();
  const mounted = useRef(true);
  const remove = useDeletePitch();
  // isPending 은 다음 렌더에야 바뀝니다. 그 사이 두 번 눌러도 요청은 한 번만 갑니다
  const sending = useRef(false);
  const busy = remove.isPending;

  // ReplaceTakeDialog 와 같습니다 — close() 를 <dialog> 가 문서에서 빠지기 전에 부릅니다 (#766)
  useLayoutEffect(() => {
    mounted.current = true;
    const dialog = ref.current;
    dialog?.showModal();
    return () => {
      mounted.current = false;
      dialog?.close();
    };
  }, []);

  const cancel = () => {
    if (!sending.current) onCancel();
  };
  const confirm = () => {
    if (sending.current) return;
    sending.current = true;
    remove.mutate(
      { pitchId: pitch.pitch_id, takeIds: pitch.takes.map((take) => take.take_id) },
      {
        onSuccess: onDeleted,
        onSettled: () => {
          sending.current = false;
        },
      },
    );
  };

  const takes = pitch.takes.length;
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={`${nameId} ${bodyId}`}
      onCancel={(e) => {
        e.preventDefault();
        cancel();
      }}
      onClose={() => {
        // 브라우저가 Esc 를 연달아 받아 닫은 경우입니다 (ReplaceTakeDialog 참고)
        if (!mounted.current || ref.current?.open) return;
        if (sending.current) ref.current?.showModal();
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
          {pitch.pitch_title}
        </p>
        <p id={bodyId} className="mt-3 text-sm leading-7">
          삭제하면 되돌릴 수 없어요.{' '}
          {takes > 0
            ? `이 피치의 Take 기록 ${takes}개와 리포트, 발표 자료 · 대본 · 평가 기준은 모두 함께 지워져요.`
            : '이 피치의 발표 자료 · 대본 · 평가 기준은 모두 함께 지워져요.'}
          {/* 설문 답은 Take 가 지워져도 연결만 끊고 남습니다 (BE take_surveys.take_id SET NULL).
              홈 목록은 Take 마다 설문 여부를 알려 주지 않고, 설문은 동의하지 않아도 리포트에서 남길 수 있어 Take 가 있으면 알립니다 */}
          {takes > 0 &&
            ` 리포트에 설문을 남기셨다면 그 답은 지워지지 않고, 피치와 떨어져 ${SURVEY_RETENTION_UNTIL}까지 따로 보관돼요.`}
        </p>
        {remove.isError && (
          <p role="alert" className="mt-3 text-sm leading-6 font-bold text-coral-deep">
            {/* 409 PITCH_BUSY 는 실패라기보다 지금은 안 된다는 답이라 "삭제하지 못했어요" 를 붙이지 않습니다 */}
            {isPitchBusy(remove.error)
              ? toMessage(remove.error)
              : `피치를 삭제하지 못했어요. ${toMessage(remove.error)}`}
          </p>
        )}
        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <Button variant="secondary" autoFocus aria-disabled={busy} onClick={cancel}>
            취소
          </Button>
          <Button aria-disabled={busy} onClick={confirm}>
            {busy ? '삭제하는 중…' : '삭제'}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
