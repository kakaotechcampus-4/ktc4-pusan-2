// 제품 `frontend/src/features/report/TakeSurvey.tsx`의 설문 창에서 복사해 시작했다. 설계서가 관리한다.
// 서버에 보내지 않는다. 보내기는 바로 onSent, 나중에 · Esc는 onClose다.
import { useId, useLayoutEffect, useRef, useState } from 'react';
import { Button } from '../Button';

/** 제품 문구와 같게 둔다. 제품이 문구를 바꾸면(QUESTION_VERSION을 올리면) 여기도 맞춘다 */
const QUESTIONS = {
  accuracy: '이번 분석 결과가 실제 내 발표와 얼마나 맞았나요?',
  usefulness: '피드백이 구체적이고 다음 연습에 도움이 됐나요?',
  comment: '부정확하거나 아쉬웠던 점이 있었나요?',
} as const;
const SCALE_ENDS = ['전혀 아니에요', '매우 그래요'] as const;
const COMMENT_LIMIT = 2000;

/** 리포트마다 받는 설문 창 (#776). 두 점수를 골라야 보낼 수 있고, 보내기 줄은 스크롤 밖 아래에 둔다 */
export function TakeSurveyDialog({
  onClose,
  onSent,
  initial = { accuracy: 0, usefulness: 0 },
}: {
  onClose: () => void;
  onSent: () => void;
  /** 설계서 샘플용. 점수를 고른 상태로 연다 */
  initial?: { accuracy: number; usefulness: number };
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const title = useId();
  const [accuracy, setAccuracy] = useState(initial.accuracy);
  const [usefulness, setUsefulness] = useState(initial.usefulness);
  const [comment, setComment] = useState('');
  const length = [...comment.trim()].length;
  const ready = accuracy > 0 && usefulness > 0 && length <= COMMENT_LIMIT;

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
        <div className="flex min-h-0 flex-col gap-5 overflow-y-auto p-4 sm:p-6">
          <div>
            <h2 id={title} className="text-xl font-bold leading-snug">
              이번 분석은 어땠나요?
            </h2>
            <p className="mt-1 text-sm leading-relaxed text-ink/80">
              1~5 중에서 골라 주세요. 답은 분석과 피드백을 고치는 데만 씁니다.
            </p>
          </div>
          <Scale
            name="accuracy"
            label={QUESTIONS.accuracy}
            value={accuracy}
            onChange={setAccuracy}
          />
          <Scale
            name="usefulness"
            label={QUESTIONS.usefulness}
            value={usefulness}
            onChange={setUsefulness}
          />
          <div className="flex min-w-0 flex-col gap-2">
            <div className="flex items-baseline justify-between gap-2">
              <label htmlFor={title + '-comment'} className="text-sm font-bold">
                {QUESTIONS.comment} (선택)
              </label>
              <span id={title + '-count'} className="shrink-0 text-xs tabular-nums text-ink/80">
                {length.toLocaleString()} / {COMMENT_LIMIT.toLocaleString()}자
              </span>
            </div>
            <textarea
              id={title + '-comment'}
              rows={3}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              aria-describedby={title + '-count'}
              placeholder="예) 말 빠르기가 실제보다 느리게 나왔어요"
              className="w-full min-w-0 resize-none border-2 border-ink bg-white p-3 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
            />
          </div>
        </div>
        <div className="flex shrink-0 items-center justify-between gap-3 border-t border-line px-4 py-3 sm:px-6">
          <Button variant="quiet" onClick={onClose}>
            나중에
          </Button>
          <Button disabled={!ready} onClick={onSent}>
            설문 보내기
          </Button>
        </div>
      </div>
    </dialog>
  );
}

/** 1~5 한 줄. 라디오라 화살표로 옮기고, 양 끝 뜻을 글자로 둔다 */
function Scale({
  name,
  label,
  value,
  onChange,
}: {
  name: string;
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <legend className="text-sm font-bold">{label}</legend>
      <div className="mt-2 grid grid-cols-5 gap-2">
        {[1, 2, 3, 4, 5].map((n) => (
          <label key={n} className="relative cursor-pointer">
            <input
              type="radio"
              name={name}
              value={n}
              checked={value === n}
              onChange={() => onChange(n)}
              aria-label={`${n}점`}
              className="peer sr-only"
            />
            <span
              aria-hidden="true"
              className={`flex min-h-11 items-center justify-center border-2 font-mono text-lg font-bold peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-ink ${value === n ? 'border-ink bg-coral-deep text-white' : 'border-line-strong bg-white text-ink'}`}
            >
              {n}
            </span>
          </label>
        ))}
      </div>
      <div className="flex justify-between text-xs text-ink/80">
        <span>1 · {SCALE_ENDS[0]}</span>
        <span>5 · {SCALE_ENDS[1]}</span>
      </div>
    </fieldset>
  );
}
