import { useEffect, useId, useRef } from 'react';
import { useCreateStore } from './createStore';
import { formatDuration, toPracticeCombo } from './lib/draft';

/** 확인한 매핑의 서버 버전을 그대로 장치 점검에 넘깁니다. */
export function StartConfirm({ onClose, onGo }: { onClose: () => void; onGo: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const draft = useCreateStore((s) => s.draft);
  const chosen = useCreateStore((s) => s.chosen);
  const practice = toPracticeCombo(draft, chosen);
  useEffect(() => {
    ref.current?.showModal();
  }, []);

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-labelledby={titleId}
      className="pitch-create m-auto w-full max-w-lg rounded-lg border border-line bg-panel p-7 text-ink backdrop:bg-ink/40"
    >
      <h2 id={titleId} className="text-2xl font-bold">
        이 조합으로 연습할까요?
      </h2>
      <p className="mt-2 text-sm text-stone">자료를 확인한 뒤 카메라와 마이크를 점검해요.</p>
      {practice ? (
        <dl className="my-6 grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 rounded-lg border border-line bg-cream/60 p-5 text-sm">
          <dt>발표 제목</dt>
          <dd className="break-words font-bold">{practice.title}</dd>
          <dt>슬라이드</dt>
          <dd>V{practice.slideVersion}</dd>
          <dt>대본</dt>
          <dd>V{practice.scriptVersion}</dd>
          <dt>목표 시간</dt>
          <dd>{formatDuration(practice.goalTimeSec)}</dd>
        </dl>
      ) : (
        <p role="alert" className="my-6 text-coral-deep">
          매핑을 다시 확인하고 저장해 주세요.
        </p>
      )}
      <div className="flex justify-end gap-3">
        <button
          type="button"
          autoFocus
          onClick={onClose}
          className="rounded border border-line-strong px-5 py-3 text-sm font-bold"
        >
          돌아가기
        </button>
        <button
          type="button"
          disabled={!practice}
          onClick={onGo}
          className="rounded bg-coral px-5 py-3 text-sm font-bold text-white disabled:bg-line"
        >
          장치 점검으로
        </button>
      </div>
    </dialog>
  );
}
