import { useEffect, useId, useRef, useState } from 'react';
import { ApiFailure } from '@/shared/api/tokenStore';
import { toMessage } from '@/shared/api/errorMessage';
import { getServiceReviewState, submitServiceReview } from '@/shared/api/serviceReview';
import { SuggestionCategorySelect } from '@/shared/ui/SuggestionCategorySelect';
import type { SuggestionCategory } from '@/types/serviceReview';
import { Button } from '@/shared/ui/Button';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';

export function ServiceReviewDialog({ onContinue }: { onContinue: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const title = useId();
  const description = useId();
  const [rating, setRating] = useState(0);
  const [suggestion, setSuggestion] = useState('');
  const [category, setCategory] = useState<SuggestionCategory>('OTHER');
  const [locked, setLocked] = useState(false);
  const length = [...suggestion.trim()].length;
  const controller = useRef<AbortController | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
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
      controller.current?.abort();
      dialog?.close();
      document.body.style.overflow = overflow;
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, []);

  async function submit() {
    if (!rating || length > 2000 || busy.current) return;
    busy.current = true;
    setPending(true);
    setError('');
    setLocked(true);
    controller.current = new AbortController();
    try {
      await submitServiceReview(
        { rating, suggestion: suggestion.trim() || null, category },
        controller.current.signal,
      );
      if (alive.current) onContinue();
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiFailure && failure.code === 'SERVICE_FEEDBACK_ALREADY_SUBMITTED') {
        // 다른 탭에서 이미 제출했으면 서버 상태를 새로 확인하고 이번 요청을 닫습니다.
        await getServiceReviewState().catch(() => undefined);
        if (alive.current) onContinue();
      } else {
        if (failure instanceof ApiFailure && failure.status === 422) setLocked(false);
        setError(
          failure instanceof ApiFailure && failure.code === 'SERVICE_FEEDBACK_NOT_ELIGIBLE'
            ? '평점을 보낼 수 있는 완료 리포트가 없어요. 닫고 다음 연습을 진행해 주세요.'
            : '보내지 못했어요. ' + toMessage(failure) + ' 같은 내용으로 다시 시도할 수 있어요.',
        );
      }
    } finally {
      busy.current = false;
      controller.current = null;
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
        <div className="min-w-0 flex-1 [&>svg]:h-auto [&>svg]:w-full [&>svg]:max-w-48">
          <PitchCoachWordmark />
        </div>
        <Button
          variant="quiet"
          size="sm"
          className="shrink-0"
          onClick={onContinue}
          aria-label="리뷰 요청 닫고 계속하기"
        >
          닫기
        </Button>
      </div>
      <h2 id={title} className="mt-6 text-xl font-bold">
        피치코치가 도움이 되었나요?
      </h2>
      <p id={description} className="mt-3 text-sm leading-relaxed text-ink/80">
        연습과 리포트 경험을 별점으로 알려주세요.
      </p>
      <fieldset disabled={pending || locked} className="my-6">
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
                className={`flex h-11 w-10 sm:h-12 sm:w-12 items-center justify-center text-3xl peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-ink ${value <= rating ? 'text-coral-deep' : 'text-stone'}`}
              >
                ★
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="mb-4 flex min-w-0 flex-col gap-2">
        <SuggestionCategorySelect
          value={category}
          onChange={setCategory}
          disabled={pending || locked}
        />
        <label htmlFor={description + '-opinion'} className="text-sm font-bold">
          의견 (선택)
        </label>
        <textarea
          id={description + '-opinion'}
          rows={4}
          value={suggestion}
          readOnly={pending || locked}
          onChange={(event) => setSuggestion(event.target.value)}
          aria-describedby={description + '-count'}
          className="w-full min-w-0 resize-y border-2 border-ink bg-white p-3 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
        />
        <p id={description + '-count'} className="text-sm text-ink/80">
          {length.toLocaleString()} / 2,000자
        </p>
      </div>
      {error && (
        <p role="alert" className="mb-4 text-sm">
          {error}
        </p>
      )}
      {pending && (
        <p role="status" className="mb-4 text-sm">
          리뷰를 보내고 있어요.
        </p>
      )}
      <div className="flex flex-col gap-3">
        <Button
          disabled={!rating || pending || length > 2000}
          onClick={() => {
            submit().catch(() => setError('다시 시도해 주세요.'));
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
