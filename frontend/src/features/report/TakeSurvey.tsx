import { useId, useLayoutEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ApiFailure } from '@/shared/api/tokenStore';
import { toMessage } from '@/shared/api/errorMessage';
import { submitTakeSurvey, takeSurveyKeys, useTakeSurvey } from '@/shared/api/takeSurvey';
import { Button } from '@/shared/ui/Button';
import type { TakeSurveyState } from '@/types/takeSurvey';

/**
 * 리포트마다 받는 설문 (#776 · BE #777). 자동으로 띄우지 않고 리포트 아래 버튼으로 엽니다 —
 * 홈 팝업으로 받으면 미룬 설문이 쌓였을 때 어느 Take 이야기인지 구별하기 어렵기 때문입니다(회의 결과).
 *
 * 문구를 고치면 `QUESTION_VERSION` 을 올립니다. BE 가 버전별로 응답을 나눠 평균 냅니다.
 */
export const QUESTION_VERSION = 1;
const SURVEY_QUESTIONS = {
  accuracy: '이번 분석 결과가 실제 내 발표와 얼마나 맞았나요?',
  usefulness: '피드백이 구체적이고 다음 연습에 도움이 됐나요?',
  comment: '부정확하거나 아쉬웠던 점이 있었나요?',
} as const;
const SCALE_ENDS = ['전혀 아니에요', '매우 그래요'] as const;
const COMMENT_LIMIT = 2000;

/** 리포트 아래 줄. 분석이 끝난 리포트에만 둡니다(BE 는 리포트가 없는 Take 를 409 로 거절합니다) */
export function TakeSurvey({ takeId, completed }: { takeId: string; completed: boolean }) {
  const state = useTakeSurvey(takeId, completed);
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState('');

  // 제출 여부를 모르면(불러오는 중 · 실패) 버튼을 두지 않습니다. 설문은 리포트를 막지 않습니다
  if (!completed || !state.data) return null;
  if (state.data.submitted)
    return (
      <p role="status" className="text-sm text-ink/80">
        {notice || '이 리포트의 설문을 보냈어요. 감사합니다.'}
      </p>
    );
  return (
    <>
      <Button variant="secondary" onClick={() => setOpen(true)}>
        이번 분석 설문 남기기
      </Button>
      {open && (
        <TakeSurveyDialog
          takeId={takeId}
          onClose={() => setOpen(false)}
          onDone={(message) => {
            setNotice(message);
            setOpen(false);
          }}
        />
      )}
    </>
  );
}

function TakeSurveyDialog({
  takeId,
  onClose,
  onDone,
}: {
  takeId: string;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const client = useQueryClient();
  const ref = useRef<HTMLDialogElement>(null);
  const title = useId();
  const [accuracy, setAccuracy] = useState(0);
  const [usefulness, setUsefulness] = useState(0);
  const [comment, setComment] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const alive = useRef(true);
  const length = [...comment.trim()].length;
  const ready = accuracy > 0 && usefulness > 0 && length <= COMMENT_LIMIT;

  // 닫기를 DOM 에서 빠지기 전에 해야 브라우저가 초점을 연 버튼으로 돌려줍니다 (#771)
  useLayoutEffect(() => {
    alive.current = true;
    const dialog = ref.current;
    dialog?.showModal();
    return () => {
      alive.current = false;
      dialog?.close();
    };
  }, []);

  async function submit() {
    if (!ready || pending) return;
    setPending(true);
    setError('');
    try {
      const survey = await submitTakeSurvey(takeId, {
        accuracy_rating: accuracy,
        usefulness_rating: usefulness,
        comment: comment.trim() || null,
        question_version: QUESTION_VERSION,
      });
      client.setQueryData<TakeSurveyState>(takeSurveyKeys.state(takeId), {
        submitted: true,
        survey,
      });
      client.invalidateQueries({ queryKey: takeSurveyKeys.pending }).catch(() => undefined);
      if (alive.current) onDone('설문을 보냈어요. 다음 분석을 고치는 데 쓸게요.');
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiFailure && failure.code === 'TAKE_SURVEY_ALREADY_SUBMITTED') {
        // 다른 탭에서 다른 답을 먼저 보냈습니다. 서버의 답을 다시 받아 "보냄"으로 바꿉니다
        await client.invalidateQueries({ queryKey: takeSurveyKeys.state(takeId) });
        client.invalidateQueries({ queryKey: takeSurveyKeys.pending }).catch(() => undefined);
        if (alive.current) onDone('이 리포트의 설문은 이미 보냈어요.');
        return;
      }
      // 입력은 그대로 두고 같은 답으로 다시 보내게 합니다 — 같은 답이면 BE 가 200 으로 받습니다
      setError(
        failure instanceof ApiFailure && failure.code === 'TAKE_SURVEY_NOT_ELIGIBLE'
          ? '이 리포트는 아직 설문을 남길 수 없어요. 분석이 끝난 뒤 다시 열어 주세요.'
          : `보내지 못했어요. ${toMessage(failure)} 같은 답으로 다시 보낼 수 있어요.`,
      );
    } finally {
      if (alive.current) setPending(false);
    }
  }

  return (
    <dialog
      ref={ref}
      aria-labelledby={title}
      onCancel={(event) => {
        event.preventDefault();
        if (!pending) onClose();
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
            label={SURVEY_QUESTIONS.accuracy}
            value={accuracy}
            onChange={setAccuracy}
            disabled={pending}
          />
          <Scale
            name="usefulness"
            label={SURVEY_QUESTIONS.usefulness}
            value={usefulness}
            onChange={setUsefulness}
            disabled={pending}
          />
          <div className="flex min-w-0 flex-col gap-2">
            <div className="flex items-baseline justify-between gap-2">
              <label htmlFor={title + '-comment'} className="text-sm font-bold">
                {SURVEY_QUESTIONS.comment} (선택)
              </label>
              <span id={title + '-count'} className="shrink-0 text-xs tabular-nums text-ink/80">
                {length.toLocaleString()} / {COMMENT_LIMIT.toLocaleString()}자
              </span>
            </div>
            <textarea
              id={title + '-comment'}
              rows={3}
              value={comment}
              readOnly={pending}
              onChange={(event) => setComment(event.target.value)}
              aria-describedby={title + '-count'}
              placeholder="예) 말 빠르기가 실제보다 느리게 나왔어요"
              className="w-full min-w-0 resize-none border-2 border-ink bg-white p-3 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
            />
          </div>
          {error && (
            <p role="alert" className="text-sm leading-relaxed">
              {error}
            </p>
          )}
        </div>
        {/* 작은 창 · 확대 화면에서도 보내기 버튼은 입력 영역의 스크롤 밖에 둡니다 */}
        <div className="flex shrink-0 items-center justify-between gap-3 border-t border-line px-4 py-3 sm:px-6">
          <Button variant="quiet" disabled={pending} onClick={onClose}>
            나중에
          </Button>
          <Button
            disabled={!ready || pending}
            onClick={() => {
              submit().catch(() => setError('다시 시도해 주세요.'));
            }}
          >
            {submitLabel(pending, Boolean(error))}
          </Button>
        </div>
      </div>
    </dialog>
  );
}

function submitLabel(pending: boolean, failed: boolean): string {
  if (pending) return '보내는 중…';
  if (failed) return '다시 보내기';
  return '설문 보내기';
}

/** 1~5 한 줄. 라디오라 화살표로 옮기고, 양 끝 뜻을 글자로 둡니다 */
function Scale({
  name,
  label,
  value,
  onChange,
  disabled,
}: {
  name: string;
  label: string;
  value: number;
  onChange: (value: number) => void;
  disabled: boolean;
}) {
  return (
    <fieldset disabled={disabled} className="flex min-w-0 flex-col gap-2">
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
