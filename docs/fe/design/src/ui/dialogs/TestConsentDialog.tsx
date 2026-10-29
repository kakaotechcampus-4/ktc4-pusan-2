// 제품 `frontend/src/features/pitch/HomeSurveyNotice.tsx`의 동의 창에서 복사해 시작했다. 설계서가 관리한다.
// 서버에 보내지 않는다. 동의는 바로 onAgree, 나중에 · Esc는 onClose다.
import { useId, useLayoutEffect, useRef } from 'react';
import { Button } from '../Button';

/** 사용자 테스트 참여 안내 (#776). 무엇을 · 왜 · 언제까지를 보여 주고 동의를 받는다. 연습은 막지 않는다 */
export function TestConsentDialog({
  onClose,
  onAgree,
}: {
  onClose: () => void;
  onAgree: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const title = useId();

  // 닫기를 DOM에서 빠지기 전에 해야 브라우저가 초점을 연 버튼으로 돌려준다
  useLayoutEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  return (
    <dialog
      ref={ref}
      aria-labelledby={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className="pitch-create fixed inset-0 m-auto max-h-[calc(100dvh_-_2rem)] w-[calc(100%_-_2rem)] max-w-lg overflow-hidden border-2 border-ink bg-panel p-0 text-ink backdrop:bg-ink/40"
    >
      <div className="flex max-h-[calc(100dvh_-_2rem_-_4px)] flex-col">
        <div className="min-h-0 overflow-y-auto p-4 text-sm leading-relaxed sm:p-6">
          <h2 id={title} className="text-xl font-bold leading-snug">
            사용자 테스트 참여 안내
          </h2>
          <dl className="mt-4 flex flex-col gap-3">
            <div>
              <dt className="font-bold">무엇을 모으나요</dt>
              <dd className="mt-1">
                리포트마다 남기는 설문 답(분석 정확도 · 피드백 유용성 · 아쉬운 점)과 연습
                기록입니다. 연습 기록에는 발표 음성을 글로 옮긴 결과와 분석 결과가 들어갑니다. 음성
                · 영상 원본은 저장하지 않습니다.
              </dd>
            </div>
            <div>
              <dt className="font-bold">왜 모으나요</dt>
              <dd className="mt-1">
                분석이 실제 발표와 맞는지 확인하고 피드백을 고치는 데 씁니다.
              </dd>
            </div>
            <div>
              <dt className="font-bold">언제까지 두나요</dt>
              <dd className="mt-1">2026년 12월 31일까지 보관한 뒤 지웁니다.</dd>
            </div>
          </dl>
          <p className="mt-4 text-ink/80">참여하지 않아도 연습과 리포트는 똑같이 쓸 수 있어요.</p>
        </div>
        <div className="flex shrink-0 items-center justify-between gap-3 border-t border-line px-4 py-3 sm:px-6">
          <Button variant="quiet" onClick={onClose}>
            나중에
          </Button>
          <Button onClick={onAgree}>동의하고 참여하기</Button>
        </div>
      </div>
    </dialog>
  );
}
