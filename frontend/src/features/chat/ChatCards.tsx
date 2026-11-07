import { useState } from 'react';
import { useNavigate } from 'react-router';
import { dimensionLabel, dimensionStatusLabel, judgeStatusLabel } from '@/shared/lib/metricLabels';
import type { Card, ReassessSide } from '@/types/chat';
import { ChipList } from './ChatChips';
import { plain, reassessRows } from './chatCardRows';
import { pickReason } from './chatLock';
import { LockedReason } from './LockedReason';
import { asGoal, goalText } from './chatGoal';
import { memoryItemState, PROFILE_KEY_LABEL } from './chatMemory';
import { useChatStore } from './chatStore';
import { useMemorySettings, useRemoveMemoryItem } from './useChatMemory';
import { UNSUPPORTED_CARD_TEXT, type Chip, type ShownCard } from './chatResponse';
import type { DecideResult, UndoResult } from './chatTransport';

/**
 * 카드 (`docs/ai/features/chat-agent.md` 12-4 F6 · F10 · F16) — 답 아래에 그립니다.
 *
 * - 버튼은 누르는 동안 그 카드별로 막고, 서버가 확정하기 전에는 성공을 표시하지 않습니다.
 * - 모르는 카드 · 빠진 칸은 버튼 없는 대체 표시("이 카드를 표시할 수 없으니 새로고침해 주세요."), 다른 카드는 그대로.
 * - 만료 · 취소된 실행의 목표 제안은 버튼을 끄고 "시간이 지나 다시 물어봐 주세요." 를 보입니다.
 * - [연습 준비로]는 고정 경로(`/pitch/{id}/device-check`)로만, 누를 때만 옮깁니다(자동 이동 없음).
 * - 글은 모두 일반 텍스트로 그립니다.
 */

export interface CardActions {
  decide: (
    actionId: string,
    decision: 'APPROVE' | 'REJECT',
    argsHash: string,
  ) => Promise<DecideResult>;
  undo: (runId: string, actionId: string) => Promise<UndoResult>;
  /** 목표 제안에서 고름 — 보낼 수 없으면 false */
  choose: (proposalId: string, option: 'USER' | 'EXPERT') => Promise<boolean>;
  /** 지금 보낼 수 있는지 — 고르기 버튼의 잠금 */
  canSend: boolean;
  /** 보낼 수 없는 이유 — 잠긴 버튼에 숨겨 읽게 합니다 */
  lockedReason?: string | null;
}

export const EXPIRED_PROPOSAL_TEXT = '시간이 지나 다시 물어봐 주세요.';
export const MOVED_TO_PRACTICE_TEXT = '연습 준비 화면으로 옮겼어요.';
export const REASSESS_TITLE = '참고 결과 — 공식 리뷰는 그대로예요';
export const REASSESS_CURRENT_TEXT = '지금 기록 기준이에요';
export const SCRIPT_SCORE_TEXT = '평가 항목이 빠져 오른 점수예요. 실력이 바뀐 건 아니에요.';
/** BE 되돌리기 409 의 기본 문구(`ChatStateInvalid.message`) */
export const UNDO_CONFLICT_TEXT = '이미 다른 목표로 바뀌었어요.';
/** FE 가 정한 문구 — 문서에 없습니다 */
export const UNDO_FAILED_TEXT = '목표를 되돌리지 못했으니 다시 시도해 주세요.';
export const NO_VERDICT = '판정 없음';

const OPTION_LABEL = { USER: '사용자 안', EXPERT: '추천 안' } as const;

/** 목표 확인 결과 — 문서 6-4 의 이름(이룸 · 못 이룸 · 알 수 없음 · 기준 불일치) */
const GOAL_RESULT_LABEL: Record<Extract<Card, { type: 'GOAL_RESULT' }>['status'], string> = {
  ACHIEVED: '이룸',
  NOT_ACHIEVED: '못 이룸',
  UNKNOWN: '알 수 없음',
  BASELINE_MISMATCH: '기준 불일치',
};

const CARD_CLASS = 'mt-2 rounded-lg border border-line-strong bg-panel px-3 py-2 text-sm leading-6';
const BUTTON_CLASS =
  'min-h-11 rounded-lg border-2 border-ink px-3 text-sm font-bold hover:bg-cream aria-disabled:cursor-not-allowed aria-disabled:border-line-strong aria-disabled:text-stone';

export function CardList({
  cards,
  runId,
  expired,
  actions,
}: {
  cards: readonly ShownCard[];
  /** 카드를 낸 실행 — 되돌리기에 씁니다 */
  runId: string | null;
  /** 그 실행이 만료 · 취소로 끝남 — 확정 전 제안의 버튼을 끕니다 */
  expired: boolean;
  actions: CardActions;
}) {
  if (cards.length === 0) return null;
  return (
    <ul aria-label="카드" className="mt-1">
      {cards.map((shown, i) => (
        <li key={i} data-card={shown.ok ? shown.card.type : 'UNSUPPORTED'}>
          {shown.ok ? (
            <CardView
              card={shown.card}
              chips={shown.chips}
              optionChips={shown.optionChips}
              runId={runId}
              expired={expired}
              actions={actions}
            />
          ) : (
            <p className={CARD_CLASS}>{UNSUPPORTED_CARD_TEXT}</p>
          )}
        </li>
      ))}
    </ul>
  );
}

function CardView({
  card,
  chips,
  optionChips,
  runId,
  expired,
  actions,
}: {
  card: Card;
  chips: Chip[];
  optionChips: Chip[][];
  runId: string | null;
  expired: boolean;
  actions: CardActions;
}) {
  switch (card.type) {
    case 'GOAL_SAVED':
      return <GoalSavedCard card={card} chips={chips} runId={runId} actions={actions} />;
    case 'GOAL_PROPOSAL':
      return (
        <GoalProposalCard
          card={card}
          optionChips={optionChips}
          expired={expired}
          actions={actions}
        />
      );
    case 'REASSESS_RESULT':
      return <ReassessCard card={card} />;
    case 'START_PRACTICE':
      return <StartPracticeCard pitchId={card.pitch_id} />;
    case 'ASK_CONFIRM':
      return <AskConfirmCard card={card} actions={actions} />;
    case 'GOAL_RESULT':
      return (
        <div className={CARD_CLASS}>
          <p className="font-bold">
            대화에서 정한 목표 · <span data-goal-result="">{GOAL_RESULT_LABEL[card.status]}</span>
          </p>
          <p>{card.reason}</p>
        </div>
      );
    case 'MEMORY_SAVED':
      return <MemorySavedCard card={card} />;
  }
}

/** FE 가 정한 문구 — 문서에 없습니다 */
export const MEMORY_REMOVED_TEXT = '지운 기억이에요.';
export const MEMORY_CHANGED_TEXT = '지금은 다른 값으로 기억하고 있어요.';
export const MEMORY_REMOVE_FAILED_TEXT = '기억을 지우지 못했으니 다시 시도해 주세요.';

/**
 * '기억했어요' (F16 · L3-45) — BE 가 저장한 값(`item_id` · `key` · `value`)을 그대로 그리고 [지우기]를 둡니다.
 * 지운 항목 · 값이 바뀐 항목의 카드는 [지우기]만 끄고 지난 메시지에 그대로 둡니다.
 * 로그인 사용자를 아직 모르면(저장소가 비어 있음) 버튼 없이 그립니다
 */
function MemorySavedCard({ card }: { card: Extract<Card, { type: 'MEMORY_SAVED' }> }) {
  const userId = useChatStore((s) => s.userId);
  return (
    <div className={CARD_CLASS} data-memory-item={card.item_id}>
      <p className="font-bold">기억했어요</p>
      <p>
        {PROFILE_KEY_LABEL[card.key]}: {card.value}
      </p>
      {userId && <MemoryRemove userId={userId} itemId={card.item_id} value={card.value} />}
    </div>
  );
}

function MemoryRemove({
  userId,
  itemId,
  value,
}: {
  userId: string;
  itemId: string;
  value: string;
}) {
  const memory = useMemorySettings(userId);
  const { remove, busy, failed } = useRemoveMemoryItem(userId, itemId);
  const state = memoryItemState(memory.data, itemId, value);
  if (state === 'removed') return <p className="text-xs text-stone">{MEMORY_REMOVED_TEXT}</p>;
  if (state === 'changed') return <p className="text-xs text-stone">{MEMORY_CHANGED_TEXT}</p>;
  return (
    <div className="mt-1">
      {/* `disabled` 로 두면 초점이 body 로 빠집니다 (#818) — aria-disabled 로 두고 누름을 무시합니다 */}
      <button
        type="button"
        aria-disabled={busy}
        onClick={() => {
          if (!busy) remove();
        }}
        className={BUTTON_CLASS}
      >
        {busy ? '지우는 중' : '지우기'}
      </button>
      {failed && (
        <p role="alert" className="mt-1 text-xs text-coral-deep">
          {MEMORY_REMOVE_FAILED_TEXT}
        </p>
      )}
    </div>
  );
}

/** 버튼 하나의 잠금 — 누르는 동안 다시 누르지 못하게 합니다 */
function useBusy() {
  const [busy, setBusy] = useState(false);
  const run = async <T,>(work: () => Promise<T>): Promise<T | undefined> => {
    if (busy) return undefined;
    setBusy(true);
    try {
      return await work();
    } finally {
      setBusy(false);
    }
  };
  return { busy, run };
}

function GoalSavedCard({
  card,
  chips,
  runId,
  actions,
}: {
  card: Extract<Card, { type: 'GOAL_SAVED' }>;
  chips: Chip[];
  runId: string | null;
  actions: CardActions;
}) {
  const { busy, run } = useBusy();
  const [result, setResult] = useState<UndoResult | null>(null);
  const done = result === 'undone';
  const undo = () => {
    if (!runId || done) return;
    run(() => actions.undo(runId, card.action_id))
      .then((r) => {
        if (r && r !== 'locked' && r !== 'stale') setResult(r);
      })
      // 통신 계층은 실패를 결과로 돌려주므로 여기 오는 것은 예상 밖입니다 — 되돌리지 못했다고 알립니다
      .catch(() => setResult('failed'));
  };
  return (
    <div className={CARD_CLASS}>
      <p className="font-bold">대화에서 정한 목표</p>
      <p>{goalText(card.goal)}</p>
      {card.reason && <p className="text-xs text-stone">{card.reason}</p>}
      <ChipList chips={chips} />
      {result === 'conflict' && (
        <p role="alert" className="text-xs text-coral-deep">
          {UNDO_CONFLICT_TEXT}
        </p>
      )}
      {result === 'failed' && (
        <p role="alert" className="text-xs text-coral-deep">
          {UNDO_FAILED_TEXT}
        </p>
      )}
      {runId && (
        <button
          type="button"
          aria-disabled={busy || done}
          onClick={undo}
          className={`${BUTTON_CLASS} mt-1`}
        >
          되돌리기
          <LockedReason reason={pickReason([done, '되돌렸어요'], [busy, '되돌리는 중이에요'])} />
        </button>
      )}
    </div>
  );
}

function GoalProposalCard({
  card,
  optionChips,
  expired,
  actions,
}: {
  card: Extract<Card, { type: 'GOAL_PROPOSAL' }>;
  optionChips: Chip[][];
  expired: boolean;
  actions: CardActions;
}) {
  const { busy, run } = useBusy();
  const locked = busy || expired || !actions.canSend;
  return (
    <div className={CARD_CLASS}>
      <p className="font-bold">
        목표 제안{' '}
        <span className="ml-1 rounded border border-line-strong px-1 text-xs">저장 전</span>
      </p>
      <ul className="mt-1 flex flex-col gap-2">
        {card.options.map((option, i) => (
          <li key={option.label} data-option={option.label}>
            <p className="font-bold">{OPTION_LABEL[option.label]}</p>
            {option.goal && <p>{goalText(option.goal)}</p>}
            <p className="text-xs text-stone">{option.reason}</p>
            <ChipList chips={optionChips[i] ?? []} />
            {/* 잴 수 없는 안(goal 없음)은 설명만 — 고를 수 없습니다 */}
            {option.goal && !expired && (
              <button
                type="button"
                aria-disabled={locked}
                onClick={() => {
                  if (!locked)
                    run(() => actions.choose(card.proposal_id, option.label)).catch(
                      () => undefined,
                    );
                }}
                className={`${BUTTON_CLASS} mt-1`}
              >
                이 안으로
                <LockedReason
                  reason={pickReason(
                    [busy, '보내는 중이에요'],
                    [locked, actions.lockedReason ?? ''],
                  )}
                />
              </button>
            )}
          </li>
        ))}
      </ul>
      {expired && <p className="mt-1 text-xs text-stone">{EXPIRED_PROPOSAL_TEXT}</p>}
    </div>
  );
}

function sideValue(v: string | number | null | undefined, label: (s: string) => string = String) {
  if (v === null || v === undefined) return NO_VERDICT;
  return typeof v === 'number' ? String(v) : label(v);
}

/** 바꾸기 전(A) · 참고 결과(B) — 점수 · 차원 상태 · 사용자 기준 판정만 나란히, 바뀐 칸은 강조 (12-4 F6) */
function ReassessCard({ card }: { card: Extract<Card, { type: 'REASSESS_RESULT' }> }) {
  const changed = new Set(card.changed_fields);
  const dims = [
    ...new Set([...Object.keys(card.before.dimensions), ...Object.keys(card.after.dimensions)]),
  ];
  const crit = (side: ReassessSide, i: number) => {
    const c = side.criteria_results[i] as { status?: unknown } | undefined;
    return typeof c?.status === 'string' ? c.status : null;
  };
  const critName = (i: number) => {
    const c = (card.before.criteria_results[i] ?? card.after.criteria_results[i]) as
      { display?: unknown } | undefined;
    return typeof c?.display === 'string' ? c.display : `기준 ${i + 1}`;
  };
  const critCount = Math.max(
    card.before.criteria_results.length,
    card.after.criteria_results.length,
  );
  const rows: { key: string; name: string; a: string; b: string; changed: boolean }[] = [
    {
      key: 'score',
      name: '점수',
      a: sideValue(card.before.score),
      b: sideValue(card.after.score),
      changed: changed.has('score'),
    },
    ...dims.map((d) => ({
      key: `dimensions.${d}`,
      name: dimensionLabel(d),
      a: sideValue(card.before.dimensions[d], dimensionStatusLabel),
      b: sideValue(card.after.dimensions[d], dimensionStatusLabel),
      changed: changed.has(`dimensions.${d}`),
    })),
    // AI 는 기준 판정이 바뀌었는지만 한 덩어리(`criteria_results`)로 알려 줘서, 줄마다 전 · 후를 직접 견줍니다
    ...Array.from({ length: critCount }, (_, i) => ({
      key: `criteria.${i}`,
      name: critName(i),
      a: sideValue(crit(card.before, i), judgeStatusLabel),
      b: sideValue(crit(card.after, i), judgeStatusLabel),
      changed: crit(card.before, i) !== crit(card.after, i),
    })),
  ];
  // 대본을 보고 한 발표로 다시 평가해 점수가 오르면 함께 알립니다
  const scriptUp =
    card.change.script_used === true &&
    card.before.score !== null &&
    card.after.score !== null &&
    card.after.score > card.before.score;
  return (
    <div className={CARD_CLASS}>
      <p className="font-bold">{REASSESS_TITLE}</p>
      {card.official_differs && <p className="text-xs text-stone">{REASSESS_CURRENT_TEXT}</p>}
      <table className="mt-1 w-full text-xs">
        <thead>
          <tr>
            <th scope="col" className="text-left font-normal text-stone">
              항목
            </th>
            <th scope="col" className="text-left font-normal text-stone">
              바꾸기 전
            </th>
            <th scope="col" className="text-left font-normal text-stone">
              참고 결과
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.key}
              data-changed={row.changed || undefined}
              className={row.changed ? 'font-bold' : undefined}
            >
              <th scope="row" className="text-left font-normal">
                {row.name}
                {row.changed && <span className="sr-only"> (바뀜)</span>}
              </th>
              <td>{row.a}</td>
              <td>{row.b}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {scriptUp && <p className="mt-1 text-xs text-coral-deep">{SCRIPT_SCORE_TEXT}</p>}
    </div>
  );
}

function StartPracticeCard({ pitchId }: { pitchId: string }) {
  const navigate = useNavigate();
  const [moved, setMoved] = useState(false);
  return (
    <div className={CARD_CLASS}>
      {moved ? (
        <p>{MOVED_TO_PRACTICE_TEXT}</p>
      ) : (
        <button
          type="button"
          onClick={() => {
            // 카드 검사(`readCard`)가 UUID 인지 확인한 ID 로 고정 경로에만 옮깁니다 — 값은 미리 채우지 않습니다
            setMoved(true);
            navigate(`/pitch/${pitchId}/device-check`);
          }}
          className={BUTTON_CLASS}
        >
          연습 준비로
        </button>
      )}
    </div>
  );
}

function AskConfirmCard({
  card,
  actions,
}: {
  card: Extract<Card, { type: 'ASK_CONFIRM' }>;
  actions: CardActions;
}) {
  const { busy, run } = useBusy();
  // 결정이 받아들여지면 카드가 사라질 때까지 버튼을 잠가 둡니다 — 다음 진행 신호가 와야 카드가 빠집니다
  const [decided, setDecided] = useState(false);
  const locked = busy || decided;
  const decidedReason = pickReason([decided, '이미 결정했어요'], [busy, '보내는 중이에요']);
  const rows =
    card.action_type === 'REASSESS'
      ? reassessRows(card.before, card.after)
      : [...new Set([...Object.keys(card.before ?? {}), ...Object.keys(card.after)])].map((k) => ({
          key: k,
          label: k,
          value: `${card.before ? `${plain(card.before[k])} → ` : ''}${plain(card.after[k])}`,
        }));
  // 목표 저장은 전 · 후가 목표(문서 5-2) — 목표 문장으로 그립니다. 모양이 다르면 받은 칸 그대로
  const after = card.action_type === 'SAVE_GOAL' ? asGoal(card.after) : null;
  const beforeGoal = card.before === null ? null : asGoal(card.before);
  const goalChange =
    after && (card.before === null || beforeGoal) ? { after, before: beforeGoal } : null;
  const decide = (decision: 'APPROVE' | 'REJECT') => {
    if (locked) return;
    // 버튼 잠금은 `run` 이 풉니다 — 예상 밖 실패면 잠그지 않고 다시 누를 수 있게 둡니다
    run(() => actions.decide(card.action_id, decision, card.args_hash))
      .then((result) => {
        if (result === 'decided') setDecided(true);
      })
      .catch(() => undefined);
  };
  const until = new Date(card.expires_at).toLocaleTimeString('ko-KR', {
    hour: 'numeric',
    minute: '2-digit',
  });
  return (
    <div className={CARD_CLASS} data-action={card.action_id}>
      <p className="font-bold">{card.title}</p>
      <p>{card.summary}</p>
      {goalChange ? (
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 text-xs" data-goal-change="">
          <dt className="text-stone">지금 목표</dt>
          <dd>{goalChange.before ? goalText(goalChange.before) : '없음'}</dd>
          <dt className="text-stone">새 목표</dt>
          <dd>{goalText(goalChange.after)}</dd>
        </dl>
      ) : (
        rows.length > 0 && (
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 text-xs" data-change-rows="">
            {rows.map((row) => (
              <div key={row.key} className="contents">
                <dt className="text-stone">{row.label}</dt>
                <dd>{row.value}</dd>
              </div>
            ))}
          </dl>
        )
      )}
      {card.notes.map((note, i) => (
        <p key={i} className="text-xs text-stone">
          {note}
        </p>
      ))}
      <p className="text-xs text-stone">{until}까지 확인해 주세요.</p>
      <div className="mt-1 flex gap-2">
        <button
          type="button"
          aria-disabled={locked}
          onClick={() => decide('APPROVE')}
          className={BUTTON_CLASS}
        >
          승인
          <LockedReason reason={decidedReason} />
        </button>
        <button
          type="button"
          aria-disabled={locked}
          onClick={() => decide('REJECT')}
          className={BUTTON_CLASS}
        >
          거절
          <LockedReason reason={decidedReason} />
        </button>
      </div>
    </div>
  );
}
