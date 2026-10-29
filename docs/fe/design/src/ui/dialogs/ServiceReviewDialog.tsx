// 제품 `frontend/src/features/pitch/ServiceReviewDialog.tsx`에서 복사해 시작했다. 설계서가 관리한다.
// 서버에 보내지 않는다. 제출 · 나중에 · 닫기 · Esc는 모두 onContinue다.
import { useEffect, useId, useRef, useState } from 'react';
import { Button } from '../Button';
import { SuggestionCategorySelect, type SuggestionCategory } from '../SuggestionCategorySelect';

/** 서비스 별점 요청 창. 입력이 길어도 제출 버튼은 스크롤 밖 아래 줄에 둔다 */
export function ServiceReviewDialog({
  onContinue,
  initialRating = 0,
}: {
  onContinue: () => void;
  /** 설계서 샘플용. 별을 고른 상태로 연다 */
  initialRating?: number;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const title = useId();
  const description = useId();
  const [rating, setRating] = useState(initialRating);
  const [suggestion, setSuggestion] = useState('');
  const [category, setCategory] = useState<SuggestionCategory>('OTHER');
  const length = [...suggestion.trim()].length;

  useEffect(() => {
    const previous = document.activeElement;
    const overflow = document.body.style.overflow;
    const dialog = ref.current;
    dialog?.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      dialog?.close();
      document.body.style.overflow = overflow;
      // 닫히면 연 버튼으로 초점을 돌려준다
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, []);

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
          <fieldset className="my-4">
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
            <SuggestionCategorySelect value={category} onChange={setCategory} disabled={false} />
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
              onChange={(event) => setSuggestion(event.target.value)}
              aria-describedby={description + '-count'}
              className="-mt-1 w-full min-w-0 resize-none border-2 border-ink bg-white p-3 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
            />
          </div>
        </div>
        <div className="flex shrink-0 items-center justify-between gap-3 border-t border-line px-4 py-3 sm:px-6">
          <Button variant="quiet" onClick={onContinue}>
            나중에
          </Button>
          <Button disabled={!rating || length > 2000} onClick={onContinue}>
            별점 제출
          </Button>
        </div>
      </div>
    </dialog>
  );
}
