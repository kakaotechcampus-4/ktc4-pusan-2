import { useId, useState, useSyncExternalStore, type ReactNode } from 'react';
import { Link } from 'react-router';
import type { ChatMessage, PlanStep, TraceItem, Verdict } from '@/types/chat';
import {
  chipLabel,
  errorText,
  GENERAL_LABEL,
  PROGRESS_DROPPED_TEXT,
  readMessage,
  readPlan,
  readTrace,
  type Chip,
  type ContractError,
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
const MISSING_INFO = '정보 없음';

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
      {done && claim.chips.length > 0 && (
        <ul aria-label="근거" className="mt-1 flex flex-wrap gap-1.5">
          {claim.chips.map((chip) => (
            <li key={chip.key}>
              <ChipView chip={chip} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const CHIP_CLASS =
  'inline-flex min-h-8 items-center rounded-full border border-line-strong bg-cream px-2.5 text-xs font-bold';

function ChipView({ chip }: { chip: Chip }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const label = chipLabel(chip);
  if (chip.type === 'RECORD' && chip.takeId)
    return (
      <Link
        to={`/takes/${chip.takeId}`}
        className={`${CHIP_CLASS} underline-offset-2 hover:underline`}
      >
        {label}
      </Link>
    );
  if (chip.type !== 'SOURCE') return <span className={CHIP_CLASS}>{label}</span>;
  // 자료 칩은 펼치면 인용 · 출처 · 라이선스. 빠진 칸은 "정보 없음" (12-4 F4)
  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((v) => !v)}
        className={CHIP_CLASS}
      >
        {label}
      </button>
      {open && (
        <dl id={id} className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 text-xs leading-5">
          <dt className="text-stone">인용</dt>
          <dd>{chip.quote ?? MISSING_INFO}</dd>
          <dt className="text-stone">출처</dt>
          <dd>{chip.source ?? MISSING_INFO}</dd>
          <dt className="text-stone">라이선스</dt>
          <dd>{chip.license ?? MISSING_INFO}</dd>
        </dl>
      )}
    </>
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

/** 지난 답 하나 — 효과 없이 그립니다 */
export function StoredAnswer({ message }: { message: ChatMessage }) {
  const answer = readMessage(message);
  const plan = readPlan(message.plan);
  const trace = readTrace(message.trace);
  return (
    <div className={AGENT_BUBBLE} data-message={message.message_id}>
      <PlanList plan={plan.items} />
      {answer.claims.map((claim) => (
        <ClaimLine key={claim.index} claim={claim} shown={claim.text} done />
      ))}
      {answer.error && <p className="mt-1 text-coral-deep">{errorText(answer.error)}</p>}
      {(plan.dropped || trace.dropped) && (
        <p className="mt-1 text-xs text-stone">{PROGRESS_DROPPED_TEXT}</p>
      )}
      <TraceList trace={trace.items} />
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
}: {
  progress: LiveProgress | null;
  typing: TypingQueue<ShownClaim>;
  announce: string;
  /** 기다리는 동안 보일 줄 — 진행 중이고 아직 문장이 없을 때 */
  waiting: ReactNode;
}) {
  const lines = useSyncExternalStore(typing.subscribe, typing.lines);
  const hasBody = progress !== null && (lines.length > 0 || (progress.plan?.length ?? 0) > 0);
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
          <TraceList trace={progress.trace ?? []} />
        </div>
      )}
      {waiting}
    </>
  );
}
