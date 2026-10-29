import { useEffect, useId, useRef, useState } from 'react';
import { ApiFailure } from '@/shared/api/tokenStore';
import { toMessage } from '@/shared/api/errorMessage';
import { getServiceReviewState, submitServiceReview } from '@/shared/api/serviceReview';
import { SuggestionCategorySelect } from '@/shared/ui/SuggestionCategorySelect';
import type { SuggestionCategory } from '@/types/serviceReview';
import { Button } from '@/shared/ui/Button';

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
      className="pitch-create fixed inset-0 m-auto max-h-[calc(100dvh_-_2rem)] w-[calc(100%_-_2rem)] max-w-lg overflow-hidden border-2 border-ink bg-panel p-0 text-ink backdrop:bg-ink/40"
    >
      <div className="flex max-h-[calc(100dvh_-_2rem_-_4px)] flex-col">
        <div className="min-h-0 overflow-y-auto p-4 sm:p-6">
          <div className="flex items-start justify-between gap-3">
            <h2 id={title} className="text-xl font-bold leading-snug">
              피치코치가 도움이 되었나요?
            </h2>
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
          <p id={description} className="mt-1 text-sm leading-relaxed text-ink/80">
            연습과 리포트 경험을 별점으로 알려주세요.
          </p>
          <fieldset disabled={pending || locked} className="my-4">
            <legend className="sr-only">서비스 만족도</legend>
            <div className="flex justify-center gap-1 sm:gap-2">
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
                    className={`flex h-11 w-11 items-center justify-center sm:h-12 sm:w-12 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-ink ${value <= rating ? 'text-coral-deep' : 'text-stone'}`}
                  >
                    <svg
                      viewBox="0 0 24 24"
                      className="h-8 w-8"
                      fill={value <= rating ? 'currentColor' : 'none'}
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeLinejoin="round"
                    >
                      <path d="m12 3 2.78 5.63 6.22.91-4.5 4.38 1.06 6.2L12 17.19l-5.56 2.93 1.06-6.2L3 9.54l6.22-.91Z" />
                    </svg>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="flex min-w-0 flex-col gap-3">
            <SuggestionCategorySelect
              value={category}
              onChange={setCategory}
              disabled={pending || locked}
            />
            <div className="flex items-baseline justify-between gap-2">
              <label htmlFor={description + '-opinion'} className="text-sm font-bold">
                의견 (선택)
              </label>
              <span id={description + '-count'} className="text-xs tabular-nums text-ink/80">
                {length.toLocaleString()} / 2,000자
              </span>
            </div>
            <textarea
              id={description + '-opinion'}
              rows={3}
              value={suggestion}
              readOnly={pending || locked}
              onChange={(event) => setSuggestion(event.target.value)}
              aria-describedby={description + '-count'}
              className="-mt-1 w-full min-w-0 resize-none border-2 border-ink bg-white p-3 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
            />
          </div>
          {error && (
            <p role="alert" className="mt-2 text-sm leading-relaxed">
              {error}
            </p>
          )}
          {pending && (
            <p role="status" className="sr-only">
              리뷰를 보내고 있어요.
            </p>
          )}
        </div>
        {/* 작은 창·확대 화면에서도 전송 버튼은 입력 영역의 스크롤 밖에 둡니다. */}
        <div className="flex shrink-0 items-center justify-between gap-3 border-t border-line px-4 py-3 sm:px-6">
          <Button variant="quiet" onClick={onContinue}>
            나중에
          </Button>
          <Button
            disabled={!rating || pending || length > 2000}
            onClick={() => {
              submit().catch(() => setError('다시 시도해 주세요.'));
            }}
          >
            {pending ? '제출 중…' : submitLabel}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
