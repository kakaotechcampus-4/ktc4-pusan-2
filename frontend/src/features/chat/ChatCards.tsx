import { useState } from 'react';
import { useNavigate } from 'react-router';
import { dimensionLabel, dimensionStatusLabel, judgeStatusLabel } from '@/shared/lib/metricLabels';
import type { Card, ProfileKey, ReassessSide } from '@/types/chat';
import { ChipList } from './ChatChips';
import { asGoal, goalText } from './chatGoal';
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

/** 코칭 프로필 항목 이름 (문서 5-2 `ProfileItem`) */
const PROFILE_KEY_LABEL: Record<ProfileKey, string> = {
  TONE: '답 말투 · 길이',
  EXPLAIN_STYLE: '설명 방식',
  FOCUS_AREA: '먼저 보고 싶은 영역',
  CONTEXT: '발표 상황',
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
      // [지우기]는 BE 기억 API 가 열리면 붙입니다 (BE 미구현, 12-3 기억)
      return (
        <div className={CARD_CLASS}>
          <p className="font-bold">기억했어요</p>
          <p>
            {PROFILE_KEY_LABEL[card.key]}: {card.value}
          </p>
        </div>
      );
  }
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
  const undo = async () => {
    if (!runId || done) return;
    const r = await run(() => actions.undo(runId, card.action_id));
    if (r && r !== 'locked' && r !== 'stale') setResult(r);
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
          onClick={() => void undo()}
          className={`${BUTTON_CLASS} mt-1`}
        >
          되돌리기
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
                  if (!locked) void run(() => actions.choose(card.proposal_id, option.label));
                }}
                className={`${BUTTON_CLASS} mt-1`}
              >
                이 안으로
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
    ...Array.from({ length: critCount }, (_, i) => ({
      key: `criteria.${i}`,
      name: critName(i),
      a: sideValue(crit(card.before, i), judgeStatusLabel),
      b: sideValue(crit(card.after, i), judgeStatusLabel),
      changed: changed.has('criteria_results'),
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
            void navigate(`/pitch/${pitchId}/device-check`);
          }}
          className={BUTTON_CLASS}
        >
          연습 준비로
        </button>
      )}
    </div>
  );
}

/** 바뀌는 내용 한 줄 — 값은 받은 그대로 글자로 */
const plain = (v: unknown) => {
  if (v === null || v === undefined) return '없음';
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v);
  return JSON.stringify(v);
};

function AskConfirmCard({
  card,
  actions,
}: {
  card: Extract<Card, { type: 'ASK_CONFIRM' }>;
  actions: CardActions;
}) {
  const { busy, run } = useBusy();
  const keys = [...new Set([...Object.keys(card.before ?? {}), ...Object.keys(card.after)])];
  // 목표 저장은 전 · 후가 목표(문서 5-2) — 목표 문장으로 그립니다. 모양이 다르면 받은 칸 그대로
  const after = card.action_type === 'SAVE_GOAL' ? asGoal(card.after) : null;
  const beforeGoal = card.before === null ? null : asGoal(card.before);
  const goalChange =
    after && (card.before === null || beforeGoal) ? { after, before: beforeGoal } : null;
  const decide = (decision: 'APPROVE' | 'REJECT') => {
    if (!busy) void run(() => actions.decide(card.action_id, decision, card.args_hash));
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
        keys.length > 0 && (
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 text-xs">
            {keys.map((k) => (
              <div key={k} className="contents">
                <dt className="text-stone">{k}</dt>
                <dd>
                  {card.before ? `${plain(card.before[k])} → ` : ''}
                  {plain(card.after[k])}
                </dd>
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
          aria-disabled={busy}
          onClick={() => decide('APPROVE')}
          className={BUTTON_CLASS}
        >
          승인
        </button>
        <button
          type="button"
          aria-disabled={busy}
          onClick={() => decide('REJECT')}
          className={BUTTON_CLASS}
        >
          거절
        </button>
      </div>
    </div>
  );
}
