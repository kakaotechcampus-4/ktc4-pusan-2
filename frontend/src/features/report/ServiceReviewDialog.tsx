import { useEffect, useId, useRef, useState } from 'react';
import { submitServiceReview } from '@/shared/api/serviceReview';
import { Button } from '@/shared/ui/Button';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';

export function ServiceReviewDialog({
  takeId,
  onContinue,
}: {
  takeId: string;
  onContinue: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const title = useId();
  const description = useId();
  const [rating, setRating] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  const busy = useRef(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    const previous = document.activeElement;
    const overflow = document.body.style.overflow;
    const dialog = ref.current;
    dialog?.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      alive.current = false;
      dialog?.close();
      document.body.style.overflow = overflow;
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, []);

  async function submit() {
    if (!rating || busy.current) return;
    busy.current = true;
    setPending(true);
    setError(false);
    try {
      await submitServiceReview(takeId, { rating });
      if (alive.current) onContinue();
    } catch {
      if (alive.current) setError(true);
    } finally {
      busy.current = false;
      if (alive.current) setPending(false);
    }
  }

  const submitLabel = error ? '다시 제출' : '별점 제출';

  return (
    <dialog
      ref={ref}
      aria-labelledby={title}
      aria-describedby={description}
      onCancel={(event) => {
        event.preventDefault();
        onContinue();
      }}
      className="pitch-create fixed inset-0 m-auto max-h-[85dvh] w-[calc(100%_-_2rem)] max-w-md overflow-y-auto border-2 border-ink bg-panel p-6 text-ink backdrop:bg-ink/40 sm:p-8"
    >
      <div className="flex items-center justify-between gap-4">
        <PitchCoachWordmark />
        <Button variant="quiet" size="sm" onClick={onContinue} aria-label="리뷰 요청 닫고 계속하기">
          닫기
        </Button>
      </div>
      <h2 id={title} className="mt-6 text-xl font-bold">
        피치코치가 도움이 되었나요?
      </h2>
      <p id={description} className="mt-3 text-sm leading-relaxed text-stone">
        연습과 리포트 경험을 별점으로 알려주세요.
      </p>
      <fieldset disabled={pending} className="my-6">
        <legend className="mb-3 text-sm font-bold">서비스 만족도</legend>
        <div className="flex justify-between gap-1">
          {[1, 2, 3, 4, 5].map((value) => (
            <label key={value} className="relative cursor-pointer">
              <input
                type="radio"
                name="service-rating"
                value={value}
                checked={rating === value}
                onChange={() => setRating(value)}
                aria-label={`${value}점`}
                className="peer sr-only"
              />
              <span
                aria-hidden="true"
                className={`flex h-11 w-10 sm:h-12 sm:w-12 items-center justify-center text-3xl peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-ink ${value <= rating ? 'text-coral' : 'text-stone'}`}
              >
                ★
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      {error && (
        <p role="alert" className="mb-4 text-sm">
          보내지 못했어요. 선택한 별점으로 다시 시도하거나 나중에 계속할 수 있어요.
        </p>
      )}
      {pending && (
        <p role="status" className="mb-4 text-sm">
          리뷰를 보내고 있어요.
        </p>
      )}
      <div className="flex flex-col gap-3">
        <Button
          disabled={!rating || pending}
          onClick={() => {
            submit().catch(() => setError(true));
          }}
        >
          {pending ? '제출 중…' : submitLabel}
        </Button>
        <Button variant="quiet" onClick={onContinue}>
          나중에
        </Button>
      </div>
    </dialog>
  );
}
