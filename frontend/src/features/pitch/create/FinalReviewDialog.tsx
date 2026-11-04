import { useEffect, useId, useRef } from 'react';
import { formatDuration, resolveChosen, type Chosen, type PitchDraft } from './lib/draft';

export function FinalReviewDialog({
  draft,
  chosen,
  onClose,
  onConfirm,
}: {
  draft: PitchDraft;
  chosen: Chosen;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const { slides, script, criteria } = resolveChosen(draft, chosen);
  useEffect(() => {
    const dialog = ref.current!;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    dialog.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      dialog.close();
      document.body.style.overflow = overflow;
      previous?.focus({ preventScroll: true });
    };
  }, []);
  const rows = [
    [
      '슬라이드',
      `${slides?.fileName ?? '발표 자료'} · V${slides?.version} · ${slides?.pageCount}장`,
    ],
    ['대본', `V${script?.version} · ${script?.blocks?.length}장 · 저장 완료`],
    ['발표 제목', draft.title],
    [
      '목표 발표시간',
      `${formatDuration(draft.timeLimitSec)} · 짧게 ${draft.lowerToleranceSec}초 / 길게 ${draft.upperToleranceSec}초 허용`,
    ],
  ];
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={() => {
        if (!ref.current?.open) onClose();
      }}
      className="m-auto h-fit max-h-[calc(100dvh-2rem)] w-[min(44rem,calc(100vw-2rem))] max-w-none overflow-auto border border-line-strong bg-panel p-5 text-ink open:flex open:flex-col open:gap-4 backdrop:bg-ink/45"
    >
      <header className="flex items-center justify-between gap-4">
        <h2 id={titleId} className="text-xl font-bold">
          발표 준비 최종 확인
        </h2>
        <button
          type="button"
          onClick={() => ref.current?.close()}
          className="min-h-11 shrink-0 border border-line-strong px-5 text-sm font-bold hover:bg-cream"
        >
          닫기
        </button>
      </header>
      <p>이 자료와 설정으로 연습할까요? 수정하려면 창을 닫고 해당 항목을 바꿔 주세요.</p>
      <dl className="grid gap-5 border-y border-line py-5">
        {rows.map(([label, value]) => (
          <div key={label} className="grid gap-1 sm:grid-cols-[8rem_1fr] sm:gap-4">
            <dt className="font-bold">{label}</dt>
            <dd className="min-w-0 break-words">{value}</dd>
          </div>
        ))}
        <div className="grid gap-1 sm:grid-cols-[8rem_1fr] sm:gap-4">
          <dt className="font-bold">평가기준</dt>
          <dd className="min-w-0 break-words">
            {criteria?.items.length ? (
              <>
                <p className="mb-2 text-sm">V{criteria.version} · 확인 완료</p>
                <ol className="list-decimal space-y-2 pl-5">
                  {criteria.items.map((item) => (
                    <li key={item.id}>{item.text}</li>
                  ))}
                </ol>
              </>
            ) : (
              '사용하지 않음'
            )}
          </dd>
        </div>
      </dl>
      <div className="flex flex-wrap justify-end gap-3">
        <button
          type="button"
          onClick={onClose}
          className="min-h-11 border border-line-strong px-5 py-3 text-sm font-bold hover:bg-cream"
        >
          돌아가서 수정
        </button>
        <button
          type="button"
          onClick={onConfirm}
          className="min-h-11 bg-coral-deep px-5 py-3 text-sm font-bold text-white hover:bg-ink"
        >
          확인하고 시작 전 점검 →
        </button>
      </div>
    </dialog>
  );
}
