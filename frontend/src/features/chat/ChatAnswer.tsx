import { useSyncExternalStore, type ReactNode } from 'react';
import type { ChatMessage, PlanStep, TraceItem, Verdict } from '@/types/chat';
import { CardList, type CardActions } from './ChatCards';
import { ChipList } from './ChatChips';
import { partialHeader } from './chatNotices';
import {
  errorText,
  GENERAL_LABEL,
  PROGRESS_DROPPED_TEXT,
  readMessage,
  readPlan,
  readTrace,
  type ContractError,
  type ShownCard,
  type ShownClaim,
} from './chatResponse';
import type { TypedLine, TypingQueue } from './chatTyping';

/**
 * 답 그리기 (`docs/ai/features/chat-agent.md` 12-4 F3 · F4 · F7 · F12 · F13).
 *
 * - 답은 문장 단위로 그리고, 문장마다 근거 칩을 답니다. 칩은 문장이 다 보인 뒤에만 답니다(F12).
 * - 문장 종류는 색만으로 가르지 않습니다 — 내 기록 · 자료는 칩 글자로, 일반 조언은 글 라벨로(F13).
 * - 판단 문장은 판정 배지와 근거 칩을 함께, 새 판단이 대체한 판단은 흐리게 "갱신된 기록을 다시 확인했어요"(F4).
 * - 모든 글은 일반 텍스트로 그립니다. 내 기록 칩은 형식이 맞는 Take ID 일 때만 리포트로 이동합니다.
 * - 할 일(`plan`)은 답 위에, 확인한 것(`trace`)은 답 아래에 접어 둡니다(F3 · F7).
 */

/** 판정 배지 글자 (12-4 F4 "맞음 · 일부 맞음 · 기록과 다름 · 확인 불가") */
const VERDICT_LABEL: Record<Verdict, string> = {
  SUPPORTED: '맞음',
  PARTLY: '일부 맞음',
  CONTRADICTED: '기록과 다름',
  UNVERIFIABLE: '확인 불가',
};

/** 할 일 상태 표시 — ☐ 할 일 · ⟳ 하는 중 · ☑ 끝 · — 건너뜀 (12-4 F3). 읽는 글은 따로 둡니다 */
const PLAN_MARK: Record<PlanStep['status'], { mark: string; label: string }> = {
  TODO: { mark: '☐', label: '할 일' },
  DOING: { mark: '⟳', label: '하는 중' },
  DONE: { mark: '☑', label: '끝' },
  SKIPPED: { mark: '—', label: '건너뜀' },
};

export const SUPERSEDED_TEXT = '갱신된 기록을 다시 확인했어요';
export const WAITING_TEXT = '기록을 확인하고 있어요';

export const AGENT_BUBBLE =
  'max-w-[88%] self-start whitespace-pre-wrap break-words rounded-xl border border-line-strong bg-white px-4 py-3 text-sm leading-6';

/** 문장 하나 — `shown` 까지 보이고, `done` 이면 칩 · 라벨을 답니다 */
function ClaimLine({ claim, shown, done }: { claim: ShownClaim; shown: string; done: boolean }) {
  const assessment = claim.assessment;
  const superseded = assessment !== null && assessment.superseded_by !== null;
  return (
    <div data-claim="" className={superseded ? 'opacity-60' : undefined}>
      {done && assessment && (
        <span
          data-verdict={assessment.verdict}
          className="mr-2 inline-block rounded border border-ink px-1.5 text-xs font-bold"
        >
          {VERDICT_LABEL[assessment.verdict]}
        </span>
      )}
      <span>{shown}</span>
      {done && superseded && <p className="mt-1 text-xs text-stone">{SUPERSEDED_TEXT}</p>}
      {done && claim.general && <p className="mt-1 text-xs text-stone">{GENERAL_LABEL}</p>}
      {done && <ChipList chips={claim.chips} />}
    </div>
  );
}

export function PlanList({ plan }: { plan: readonly PlanStep[] }) {
  if (plan.length === 0) return null;
  return (
    <ol aria-label="할 일" className="mb-2 text-xs leading-5">
      {plan.map((step, i) => (
        <li key={i} data-plan-status={step.status}>
          <span aria-hidden="true">{PLAN_MARK[step.status].mark} </span>
          <span className="sr-only">{PLAN_MARK[step.status].label}: </span>
          {step.step}
        </li>
      ))}
    </ol>
  );
}

/** 확인한 것 — 기본은 접고, 펼치면 단계별로 (12-4 F7) */
export function TraceList({ trace }: { trace: readonly TraceItem[] }) {
  if (trace.length === 0) return null;
  return (
    <details className="mt-2 text-xs leading-5">
      <summary className="cursor-pointer text-stone">확인한 것</summary>
      <ol className="mt-1 list-decimal pl-5">
        {trace.map((item) => (
          <li key={item.seq}>
            {item.args_summary} → {item.result_summary}
          </li>
        ))}
      </ol>
    </details>
  );
}

/** 만료 · 취소로 끝난 실행 — 확정 전 제안의 버튼을 끕니다 (12-4 F6) */
const isExpired = (status: ChatMessage['status']) => status === 'EXPIRED' || status === 'CANCELLED';

/** 지난 답 하나 — 효과 없이 그립니다 */
export function StoredAnswer({
  message,
  actions,
  onContinue,
}: {
  message: ChatMessage;
  actions: CardActions;
  /** [이어서 확인] — 중간까지만 답한 마지막 답에만 둡니다 (12-4 F8 `PARTIAL`) */
  onContinue?: () => void;
}) {
  const answer = readMessage(message);
  const plan = readPlan(message.plan);
  const trace = readTrace(message.trace);
  const header = partialHeader(message.status, answer.claims.at(-1)?.text ?? null, plan.items);
  return (
    <div className={AGENT_BUBBLE} data-message={message.message_id}>
      {header && (
        <p className="mb-1 text-xs font-bold text-stone" data-partial="">
          {header}
        </p>
      )}
      <PlanList plan={plan.items} />
      {answer.claims.map((claim) => (
        <ClaimLine key={claim.index} claim={claim} shown={claim.text} done />
      ))}
      {answer.error && <p className="mt-1 text-coral-deep">{errorText(answer.error)}</p>}
      {(plan.dropped || trace.dropped) && (
        <p className="mt-1 text-xs text-stone">{PROGRESS_DROPPED_TEXT}</p>
      )}
      <CardList
        // 지난 메시지의 승인 확인 카드는 버튼 없는 대체 표시로 바뀌어 옵니다(`readMessage`). "새로고침해 주세요" 는
        // 새로고침해도 같아 틀린 안내라 그리지 않습니다 — 만료 · 취소 사정은 BE 가 저장한 고정 문장이 알립니다
        cards={answer.cards.filter((c) => c.ok || c.type !== 'ASK_CONFIRM')}
        runId={message.run_id}
        expired={isExpired(message.status)}
        actions={actions}
      />
      <TraceList trace={trace.items} />
      {header && onContinue && (
        <button
          type="button"
          aria-disabled={!actions.canSend}
          onClick={() => {
            if (actions.canSend) onContinue();
          }}
          className="mt-2 min-h-11 rounded-lg border border-line-strong px-3 text-xs font-bold hover:bg-cream aria-disabled:cursor-not-allowed aria-disabled:text-stone"
        >
          이어서 확인
        </button>
      )}
    </div>
  );
}

export interface LiveProgress {
  runId: string;
  conversationId: string;
  plan: PlanStep[] | null;
  trace: TraceItem[] | null;
  contractError: ContractError | null;
  progressDropped: boolean;
  /** 승인 대기 중 카드 — 문장이 다 보인 뒤에 답니다 */
  cards: ShownCard[];
}

/**
 * 지금 실행의 답 — 받은 문장이 글자 효과로 보입니다(F12). 화면 읽기 프로그램에는 문장이 다 보일 때마다
 * 그 문장 하나만 알립니다(`announce`, L3-37) — 글자마다 읽히지 않게 효과 글에는 aria-live 를 두지 않습니다
 */
export function LiveAnswer({
  progress,
  typing,
  announce,
  waiting,
  actions,
}: {
  progress: LiveProgress | null;
  typing: TypingQueue<ShownClaim>;
  announce: string;
  /** 기다리는 동안 보일 줄 — 진행 중이고 아직 문장이 없을 때 */
  waiting: ReactNode;
  actions: CardActions;
}) {
  const lines = useSyncExternalStore(typing.subscribe, typing.lines);
  const hasBody =
    progress !== null &&
    (lines.length > 0 || (progress.plan?.length ?? 0) > 0 || progress.cards.length > 0);
  // 판단 문장이 먼저, 확인 카드는 그 뒤에 — 문장이 다 보인 뒤에 답니다 (12-4 F3)
  const typed = lines.every((line) => line.done);
  return (
    <>
      <p aria-live="polite" className="sr-only" data-chat-announce="">
        {announce}
      </p>
      {hasBody && (
        <div className={AGENT_BUBBLE} data-live-run={progress.runId}>
          <PlanList plan={progress.plan ?? []} />
          {lines.map((line: TypedLine<ShownClaim>) => (
            <ClaimLine key={line.index} claim={line.item} shown={line.shown} done={line.done} />
          ))}
          {progress.contractError && (
            <p className="mt-1 text-coral-deep">{errorText(progress.contractError)}</p>
          )}
          {progress.progressDropped && (
            <p className="mt-1 text-xs text-stone">{PROGRESS_DROPPED_TEXT}</p>
          )}
          {typed && (
            <CardList
              cards={progress.cards}
              runId={progress.runId}
              expired={false}
              actions={actions}
            />
          )}
          <TraceList trace={progress.trace ?? []} />
        </div>
      )}
      {waiting}
    </>
  );
}
