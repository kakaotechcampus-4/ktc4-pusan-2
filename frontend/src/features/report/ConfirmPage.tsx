import { useEffect, useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router';
import { useConfirmations, useSendConfirmations } from '@/shared/api/confirmation';
import { toMessage } from '@/shared/api/errorMessage';
import { useTakeReport } from '@/shared/api/report';
import { ApiFailure } from '@/shared/api/tokenStore';
import { Button, ButtonLink } from '@/shared/ui/Button';
import type { ConfirmationItem, Confirmations } from '@/types/confirmation';
import { DuskScreen, StatusLine } from './DuskScreen';
import {
  allPicked,
  itemKey,
  remainingLabel,
  splitAround,
  toAnswers,
  type Choice,
} from './confirmFormat';

/**
 * 대본 확인 — 발표 뒤 인식이 헷갈린 말(비슷한 말)을 "대본대로 말함 / 들린 대로 말함"으로 묻습니다.
 * 분석 대기(`ProcessingPage`)가 확인 대기일 때 버튼으로 옵니다. 같은 노을 화면을 씁니다.
 *
 * 답은 한 번에 보내고, 보내면(건너뛰어도) 분석 대기로 돌아갑니다. 고르지 않은 말 · '잘 모르겠어요'는
 * 보류로 남고, 내용 점수는 잠정값을 유지합니다 (`docs/ai/features/script-coverage-evaluation.md` 6-3).
 * 확인 대기가 시작된 지 10분이 지나면 답 없이 그대로 진행됩니다 (같은 문서 12절).
 *
 * BE #528의 확인 API를 사용합니다 (`types/confirmation.ts`).
 * 녹음 다시 듣기는 없습니다 — 음성을 어디에도 저장하지 않아(CLAUDE.md 4번) 들려줄 소리가 없습니다.
 * 대신 그 말이 든 문장을 보여 줍니다.
 */
export function ConfirmPage() {
  const { takeId = '' } = useParams();
  const report = useTakeReport(takeId);
  const confirmations = useConfirmations(takeId);

  if (!takeId) return <Navigate replace to="/" />;

  return (
    <DuskScreen report={report.data}>
      <ConfirmContent key={takeId} takeId={takeId} confirmations={confirmations} />
    </DuskScreen>
  );
}

function ConfirmContent({
  takeId,
  confirmations,
}: {
  takeId: string;
  confirmations: ReturnType<typeof useConfirmations>;
}) {
  // 재조회로 폼이 잠시 로딩·오류 화면으로 바뀌어도 선택한 답은 유지합니다.
  const [picks, setPicks] = useState<Record<string, Choice>>({});
  const navigate = useNavigate();
  const send = useSendConfirmations(takeId);
  const submit = (answers: ReturnType<typeof toAnswers>) =>
    send.mutate(
      { answers },
      { onSuccess: () => navigate(`/takes/${takeId}/processing`, { replace: true }) },
    );
  // 재조회가 제출 중인 폼을 없애거나 제출 오류를 가리지 않게 합니다.
  const keepSubmission = send.isPending || send.error !== null;

  if (confirmations.error && !keepSubmission)
    return (
      <div role="alert" className="flex flex-col items-center gap-4">
        <p className="text-lg font-bold">확인할 말을 불러오지 못했어요.</p>
        <p className="text-sm">{toMessage(confirmations.error)}</p>
        <div className="flex flex-wrap justify-center gap-3">
          <Button onClick={() => confirmations.refetch().catch(() => undefined)}>
            다시 불러오기
          </Button>
          <ButtonLink to={`/takes/${takeId}/processing`} variant="secondary">
            분석 대기로
          </ButtonLink>
        </div>
      </div>
    );

  if (!confirmations.data || (confirmations.isFetching && !keepSubmission))
    return <StatusLine text="확인할 말을 불러오는 중" />;

  if (confirmations.data.items.length === 0 && !keepSubmission)
    return (
      <Closed
        takeId={takeId}
        title="확인할 말이 없어요"
        text="확인 시간이 끝났거나 이미 답했어요. 분석은 그대로 이어져요."
      />
    );

  return (
    <ConfirmForm
      takeId={takeId}
      data={confirmations.data}
      picks={picks}
      send={send}
      onSubmit={submit}
      onChoose={(key, choice) => setPicks((previous) => ({ ...previous, [key]: choice }))}
    />
  );
}

/** 더 답할 수 없을 때 — 분석 대기로 돌려보냅니다. 거기서 끝났으면 리포트로 넘어갑니다 */
function Closed({ takeId, title, text }: { takeId: string; title: string; text: string }) {
  return (
    <div role="alert" className="flex flex-col items-center gap-4">
      <p className="text-lg font-bold">{title}</p>
      <p className="text-sm leading-relaxed">{text}</p>
      <ButtonLink to={`/takes/${takeId}/processing`}>분석 대기로</ButtonLink>
    </div>
  );
}

const TICK_MS = 1000;

function ConfirmForm({
  takeId,
  data,
  picks,
  onChoose,
  send,
  onSubmit,
}: {
  takeId: string;
  data: Confirmations;
  picks: Record<string, Choice>;
  onChoose: (key: string, choice: Choice) => void;
  send: ReturnType<typeof useSendConfirmations>;
  onSubmit: (answers: ReturnType<typeof toAnswers>) => void;
}) {
  // 받은 순간부터 셉니다 — 백그라운드 탭에서도 맞게 performance.now() 기준 (CLAUDE.md 개발 중 함정)
  const [deadline] = useState(() => performance.now() + (data.remaining_ms ?? 0));
  const [now, setNow] = useState(() => performance.now());
  const left = deadline - now;
  const expired = left <= 0;
  useEffect(() => {
    if (expired) return;
    const id = window.setInterval(() => setNow(performance.now()), TICK_MS);
    return () => window.clearInterval(id);
  }, [expired]);

  const closed = send.error instanceof ApiFailure && send.error.code === 'CONFIRMATION_CLOSED';
  // 기한 안에 보낸 답은 시간이 지나도 서버 결과를 기다립니다.
  if (send.isPending) return <StatusLine text="답을 반영하는 중" />;
  if (closed)
    return <Closed takeId={takeId} title="확인을 받을 수 없어요" text={toMessage(send.error)} />;
  if (expired)
    return <Closed takeId={takeId} title="확인 시간이 끝났어요" text="답 없이 그대로 진행돼요." />;

  return (
    <>
      <h1 className="text-lg font-bold sm:text-xl">비슷하게 들린 말이 있어요</h1>
      <p className="text-sm leading-relaxed">
        확인할 항목이 {data.items.length}개 있어요. 현재 내용 점수는 이 부분을 대본대로 말했다고
        가정한 잠정값이에요. 어떻게 말했는지 답해 주시면 다시 계산하며, 점수가 달라질 수 있어요.
      </p>
      <p className="text-sm leading-relaxed">
        잘 모르겠거나 건너뛰면 해당 항목은 보류로 남고 내용 점수는 잠정값을 유지해요. 보류한 부분은
        수치 정확도와 대본 충실도 계산에서 제외돼요.
      </p>
      <p role="timer" className="tabular font-mono text-xs text-dusk-accent">
        남은 시간 {remainingLabel(left)}
      </p>

      <ol className="flex w-full flex-col gap-3 text-left">
        {data.items.map((item) => (
          <ItemCard
            key={itemKey(item)}
            item={item}
            choice={picks[itemKey(item)]}
            onChoose={(choice) => onChoose(itemKey(item), choice)}
          />
        ))}
      </ol>

      {send.error && (
        <p role="alert" className="text-sm">
          {toMessage(send.error)}
        </p>
      )}
      <div className="flex flex-wrap justify-center gap-3">
        <Button
          disabled={!allPicked(data.items, picks) || send.isPending}
          onClick={() => onSubmit(toAnswers(data.items, picks))}
        >
          답 보내기
        </Button>
        <Button variant="secondary" disabled={send.isPending} onClick={() => onSubmit([])}>
          건너뛰기
        </Button>
      </div>
    </>
  );
}

function ItemCard({
  item,
  choice,
  onChoose,
}: {
  item: ConfirmationItem;
  choice: Choice | undefined;
  onChoose: (c: Choice) => void;
}) {
  const options: { value: Choice; label: string; word: string | null }[] = [
    { value: 'as_script', label: '대본대로 말했어요', word: item.script_text },
    { value: 'as_stt', label: '들린 대로 말했어요', word: item.stt_text },
    { value: 'unsure', label: '잘 모르겠어요', word: null },
  ];
  let kindLabel = '비슷한 말';
  if (item.kind === 'number') kindLabel = '숫자';
  if (item.kind === 'word') kindLabel = '단어';

  return (
    <li className="border-2 border-dusk-line p-3">
      <p className="font-mono text-xs font-bold text-dusk-accent">
        {item.slide_number}번 장 · {kindLabel}
      </p>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm leading-relaxed">
        <dt className="text-xs leading-6 text-dusk-accent">대본</dt>
        <dd>
          <Marked sentence={item.script_sentence} text={item.script_text} />
        </dd>
        <dt className="text-xs leading-6 text-dusk-accent">들린 말</dt>
        <dd>
          <Marked sentence={item.stt_sentence} text={item.stt_text} />
        </dd>
      </dl>

      <fieldset className="mt-3">
        <legend className="sr-only">{item.slide_number}번 장에서 어떻게 말했나요?</legend>
        <div className="flex flex-col gap-1.5">
          {options.map((o) => (
            <label
              key={o.value}
              className="flex cursor-pointer items-center gap-2 border-2 border-dusk-line px-3 py-2 text-sm has-checked:border-dusk-accent has-focus-visible:outline-2 has-focus-visible:outline-dusk-accent"
            >
              <input
                type="radio"
                name={`confirm-${itemKey(item)}`}
                value={o.value}
                checked={choice === o.value}
                onChange={() => onChoose(o.value)}
                className="accent-dusk-accent"
              />
              <span className="font-bold">{o.label}</span>
              {o.word !== null && <span className="text-dusk-accent">‘{o.word}’</span>}
            </label>
          ))}
        </div>
      </fieldset>
    </li>
  );
}

/** 문장 속 그 말을 강조합니다. 문장이 없거나 말이 문장에 없으면 말만 보여 줍니다 */
function Marked({ sentence, text }: { sentence: string | null; text: string }) {
  const parts = splitAround(sentence, text);
  const mark = <mark className="bg-dusk-accent px-0.5 font-bold text-dusk">{text}</mark>;
  if (!parts) return mark;
  return (
    <>
      {parts[0]}
      {mark}
      {parts[2]}
    </>
  );
}
