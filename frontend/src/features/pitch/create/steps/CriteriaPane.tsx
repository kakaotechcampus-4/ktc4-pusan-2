import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { toMessage } from '@/shared/api/errorMessage';
import { parseStandards, saveStandards } from '@/shared/api/standards';
import { isCurrentDraft, useCreateStore } from '../createStore';
import { AlertIcon, CheckIcon, DocIcon, RefreshIcon } from '../icons';
import { fromStandardsPosted } from '../lib/beAdapter';
import { criteriaToSave, pendingVersion, type CriteriaVersion } from '../lib/draft';
import { NeedPitch } from './NeedPitch';
import { PaneHeading } from './PaneHeading';

/**
 * 목업 평가기준 — 왼쪽에 자연어로 한 번에 적고, 오른쪽에서 정리된 결과를 고쳐 저장합니다.
 *
 * ── 정리 → 고치기 → 저장 (BE #192) ───────────────────────────────────
 *   정리   `POST /pitches/add/{id}/standards` — 서버(→ AI)가 나누기만 하고 저장하지 않습니다
 *   고치기 오른쪽 목록에서 문장 수정 · 추가 · 삭제. 아직 서버에는 없습니다 ("저장 안 됨")
 *   저장   `POST /pitches/{id}/standards` — 고친 목록을 확정합니다. 이 버전부터 연습에 씁니다
 *
 * 저장한 버전은 고치지 않습니다. 다시 고치려면 "수정하기"로 새 버전을 만듭니다 (다른 갈래와 같은 규칙).
 * 저장 전에 다시 정리하면 고친 목록은 새 정리 결과로 바뀝니다.
 * 발표시간은 여기가 아니라 발표정보에서 정합니다.
 */

function parseErrorMessage(error: unknown): string {
  return `기준을 정리하지 못했어요. ${toMessage(error)}`;
}

const PLACEHOLDER = [
  '이번 발표에서 확인하고 싶은 기준을 자유롭게 적어 주세요.',
  '예: 시장 규모와 출처를 말하고, 채움말은 5회 이하로 줄이고 싶어요.',
].join('\n');

function parseButtonLabel(pending: boolean, hasItems: boolean): string {
  if (pending) return '정리하는 중…';
  if (hasItems) return '수정해서 다시 정리';
  return '평가기준 정리';
}

/** 처음 정리는 꽉 찬 버튼, 결과가 있으면 테두리 버튼 — 저장이 다음 할 일이 되도록 */
function parseButtonTone(hasItems: boolean): string {
  if (hasItems) return 'border border-coral bg-white text-coral hover:bg-coral-wash';
  return 'bg-coral text-white hover:bg-coral-deep';
}

/**
 * 왼쪽 아래 상자. 서버가 넣지 않은 내용이 있으면 그것을, 없으면 작성 요령을 보여 줍니다.
 *
 * `except_standard` 는 AI 의 `excluded` — **연습에서 측정할 수 없는 항목**입니다 (자료 디자인 ·
 * 질의응답 · 발표 시간 등, AI `evaluation-criteria/prompt.py`). 글이 모호해서 빠진 게 아니므로
 * 경고가 아니라 안내로 보여 줍니다. 정상 입력에도 자주 나옵니다.
 */
function Guide({ except }: { except: string | null }) {
  if (except) {
    return (
      <div role="status" className="rounded-lg border border-line bg-panel px-4 py-3">
        <p className="flex items-center gap-2 text-sm font-bold">
          <AlertIcon />
          평가기준에 넣지 않은 내용
        </p>
        <p className="mt-1.5 pl-6 text-sm leading-relaxed text-stone">
          연습 중에 측정할 수 없는 항목이라 평가기준에서 뺐어요. 발표 시간은 발표정보에서 정해요.
        </p>
        <p className="mt-1 pl-6 text-sm font-bold leading-relaxed">“{except}”</p>
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-line bg-panel px-4 py-3">
      <p className="flex items-center gap-2 text-sm font-bold">
        <AlertIcon />
        작성 안내
      </p>
      <p className="mt-1.5 pl-6 text-sm leading-relaxed text-stone">
        구체적인 행동이나 목표를 적어 주세요.
        <br />
        정리 과정에서 반영하지 못한 내용이 있으면 이유와 안내를 이곳에 알려드려요.
      </p>
    </div>
  );
}

function Writer({ criteria, pitchId }: { criteria: CriteriaVersion | null; pitchId: string }) {
  const applyParsedCriteria = useCreateStore((s) => s.applyParsedCriteria);
  const draftId = useCreateStore((s) => s.draftId);
  const [text, setText] = useState(criteria?.sourceText ?? '');

  const parse = useMutation({
    // 정리를 시작한 작성(draftId)을 함께 넘깁니다. 그사이 새 피치를 시작했으면 결과를 버립니다
    mutationFn: async ({ source }: { source: string; draftId: string }) =>
      fromStandardsPosted(await parseStandards(pitchId, source)),
    // 결과는 버전에 담습니다. 처음 정리하면 버전이 새로 생기며 이 칸이 다시 그려지는데,
    // 응답을 여기(useMutation)에만 두면 그 순간 "넣지 못한 내용"이 사라집니다
    onSuccess: (res, { source, draftId: from }) => {
      if (!isCurrentDraft(from)) return;
      applyParsedCriteria(criteria?.version ?? null, { sourceText: source, ...res });
    },
    onError: (error) => console.error('[평가기준] 정리하지 못했습니다', error),
  });

  const hasItems = (criteria?.items.length ?? 0) > 0;
  const except = criteria?.exceptText ?? null;

  return (
    <section className="flex min-w-0 flex-col gap-3 rounded-lg border border-line bg-white p-4 xl:p-5">
      <div>
        <h3 className="text-lg font-bold">평가기준 작성</h3>
        <p className="mt-1 text-sm text-stone">문장이나 목록으로 자유롭게 작성해 주세요.</p>
      </div>

      <textarea
        aria-label="평가기준 작성"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={PLACEHOLDER}
        rows={5}
        className="min-h-32 w-full resize-y rounded-lg border border-line-strong bg-white p-3 text-sm leading-relaxed outline-none placeholder:text-stone focus:border-ink"
      />

      <button
        type="button"
        disabled={text.trim() === '' || parse.isPending}
        onClick={() => parse.mutate({ source: text, draftId })}
        className={[
          'flex h-11 shrink-0 items-center justify-center gap-2 rounded-lg text-sm font-bold',
          'disabled:cursor-not-allowed disabled:border-transparent disabled:bg-line disabled:text-stone',
          parseButtonTone(hasItems),
        ].join(' ')}
      >
        <RefreshIcon />
        {parseButtonLabel(parse.isPending, hasItems)}
      </button>

      {parse.isError && (
        <p role="alert" className="text-sm font-bold text-coral">
          {parseErrorMessage(parse.error)}
        </p>
      )}

      <Guide except={except} />
      {except && (
        <p className="text-xs text-stone">
          측정할 수 있는 행동(속도 · 음량 · 시선 등)으로 바꿔 적으면 기준에 넣을 수 있어요.
        </p>
      )}
    </section>
  );
}

function Result({ criteria, pitchId }: { criteria: CriteriaVersion | null; pitchId: string }) {
  const items = criteria?.items ?? [];
  const editable = criteria !== null && !criteria.saved;

  return (
    <section className="flex min-w-0 flex-col rounded-lg border border-line bg-white p-4 xl:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2.5">
          <h3 className="text-lg font-bold">정리된 평가기준</h3>
          <span className="tabular rounded-md bg-panel px-2.5 py-1 text-xs font-bold text-stone">
            {items.length}개 항목
          </span>
        </div>
        <SavedBadge criteria={criteria} />
      </div>

      {items.length === 0 && !editable ? (
        <div className="flex min-h-48 flex-1 flex-col items-center justify-center gap-2 py-6 text-center">
          <DocIcon className="mb-2 h-9 w-9 stroke-stone" />
          <p className="font-bold">아직 정리된 평가기준이 없어요</p>
          <p className="text-sm text-stone">기준을 작성하고 정리 버튼을 눌러 주세요.</p>
        </div>
      ) : (
        // 개수 제한이 없어 높이를 잡아 둡니다 — 항목 5개쯤 보이고 나머지는 이 안에서 스크롤합니다.
        // 높이가 없으면 목록이 카드를 늘려 화면 아래로 밀려납니다
        <ol className="mt-4 flex max-h-88 flex-col gap-2.5 overflow-y-auto pr-1">
          {items.map((item, i) =>
            editable ? (
              <EditableItem key={item.id} version={criteria.version} item={item} index={i} />
            ) : (
              <li
                key={item.id}
                className="flex items-center gap-4 rounded-lg border border-line bg-white px-3 py-3"
              >
                <ItemNumber index={i} />
                <span className="min-w-0 text-sm [overflow-wrap:anywhere]">{item.text}</span>
              </li>
            ),
          )}
        </ol>
      )}

      {editable ? (
        <SaveBar criteria={criteria} pitchId={pitchId} />
      ) : (
        <ReviseBar criteria={criteria} />
      )}
    </section>
  );
}

function ItemNumber({ index }: { index: number }) {
  return (
    <span className="tabular flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-panel text-sm font-bold">
      {String(index + 1).padStart(2, '0')}
    </span>
  );
}

/** 저장 안 한 목록은 코랄, 저장한 목록은 회색 표시 — 사이드바의 발표정보 표시와 같은 말입니다 */
function SavedBadge({ criteria }: { criteria: CriteriaVersion | null }) {
  if (!criteria || criteria.items.length === 0) return null;
  if (criteria.saved) {
    return (
      <span className="flex items-center gap-1.5 rounded-md border border-line-strong bg-panel px-2.5 py-1 text-xs font-bold">
        <CheckIcon />V{criteria.version} 저장됨
      </span>
    );
  }
  return <span className="text-xs font-bold text-coral">저장 안 됨</span>;
}

/** AI 가 정리한 문장 한 줄. 사용자가 고치거나 지울 수 있습니다 */
function EditableItem({
  version,
  item,
  index,
}: {
  version: number;
  item: { id: string; text: string };
  index: number;
}) {
  const editCriterion = useCreateStore((s) => s.editCriterion);
  const removeCriterion = useCreateStore((s) => s.removeCriterion);
  return (
    <li className="flex items-center gap-3 rounded-lg border border-line bg-white px-3 py-2">
      <ItemNumber index={index} />
      <input
        aria-label={`평가기준 ${index + 1}`}
        value={item.text}
        onChange={(e) => editCriterion(version, item.id, e.target.value)}
        placeholder="기준을 적어 주세요"
        className="h-9 min-w-0 flex-1 rounded-md border border-transparent px-2 text-sm outline-none placeholder:text-stone hover:border-line focus:border-ink"
      />
      <button
        type="button"
        aria-label={`평가기준 ${index + 1} 삭제`}
        onClick={() => removeCriterion(version, item.id)}
        className="shrink-0 rounded-md px-2 py-1 text-sm font-bold text-stone hover:bg-panel hover:text-ink"
      >
        삭제
      </button>
    </li>
  );
}

/** 저장 전: 기준 추가 · 저장. 빈 항목은 빼고 보냅니다 */
function SaveBar({ criteria, pitchId }: { criteria: CriteriaVersion; pitchId: string }) {
  const addCriterion = useCreateStore((s) => s.addCriterion);
  const markCriteriaSaved = useCreateStore((s) => s.markCriteriaSaved);
  const draftId = useCreateStore((s) => s.draftId);
  const standards = criteriaToSave(criteria);

  const save = useMutation({
    // 정리와 같습니다 — 저장하는 사이 새 피치를 시작했으면 결과를 새 작성에 넣지 않습니다
    mutationFn: ({ list }: { list: string[]; draftId: string }) => saveStandards(pitchId, list),
    onSuccess: (_res, { list, draftId: from }) => {
      if (!isCurrentDraft(from)) return;
      markCriteriaSaved(criteria.version, list);
    },
    onError: (error) => console.error('[평가기준] 저장하지 못했습니다', error),
  });

  return (
    <div className="mt-3 flex flex-col gap-3 border-t border-line pt-3">
      <button
        type="button"
        onClick={() => addCriterion(criteria.version)}
        className="self-start rounded-md px-2 py-1 text-sm font-bold text-stone hover:bg-panel hover:text-ink"
      >
        + 기준 추가
      </button>
      {save.isError && (
        <p role="alert" className="text-sm font-bold text-coral">
          평가기준을 저장하지 못했어요. {toMessage(save.error)}
        </p>
      )}
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-stone">
          {standards.length === 0
            ? '저장할 기준을 하나 이상 적어 주세요.'
            : '확인하고 저장해야 연습에 쓰여요. 빈 줄은 빼고 저장돼요.'}
        </p>
        <button
          type="button"
          disabled={standards.length === 0 || save.isPending}
          onClick={() => save.mutate({ list: standards, draftId })}
          className="h-10 shrink-0 rounded-lg bg-coral px-5 text-sm font-bold text-white hover:bg-coral-deep disabled:cursor-not-allowed disabled:bg-line disabled:text-stone"
        >
          {save.isPending ? '저장하는 중…' : '평가기준 저장'}
        </button>
      </div>
    </div>
  );
}

/** 저장 후: 저장한 목록은 고정이라, 고치려면 새 버전을 만듭니다 */
function ReviseBar({ criteria }: { criteria: CriteriaVersion | null }) {
  const reviseCriteria = useCreateStore((s) => s.reviseCriteria);
  const select = useCreateStore((s) => s.select);
  // 저장 안 한 버전이 이미 있으면 새로 만들지 않습니다 — 그 버전을 마저 저장하면 됩니다
  const pending = useCreateStore((s) => pendingVersion(s.draft, 'criteria'));
  if (!criteria || criteria.items.length === 0) {
    return (
      <p className="mt-4 border-t border-line pt-4 text-xs text-stone">
        정리한 뒤 목록을 고치고 저장할 수 있어요.
      </p>
    );
  }
  return (
    <div className="mt-4 flex items-center justify-between gap-3 border-t border-line pt-4">
      <p className="text-xs text-stone">
        {pending === null
          ? '저장한 기준은 바뀌지 않아요. 고치면 새 버전으로 저장돼요.'
          : `저장하지 않은 V${pending}이 있어요. 그 버전을 마저 저장해 주세요.`}
      </p>
      <button
        type="button"
        onClick={() =>
          pending === null ? reviseCriteria(criteria.version) : select('criteria', pending)
        }
        className="h-10 shrink-0 rounded-lg border border-coral bg-white px-4 text-sm font-bold text-coral hover:bg-coral-wash"
      >
        {pending === null ? '수정하기' : `V${pending} 열기`}
      </button>
    </div>
  );
}

export function CriteriaPane({ criteria }: { criteria: CriteriaVersion | null }) {
  const pitchId = useCreateStore((s) => s.pitchId);
  return (
    <div className="flex flex-1 flex-col">
      <PaneHeading
        title="평가기준"
        subtitle="원하는 기준을 한 번에 적고, 정리된 결과를 확인해 주세요."
      />
      {pitchId === null ? (
        <NeedPitch what="평가기준" />
      ) : (
        <div className="grid grid-cols-1 items-stretch gap-4 lg:grid-cols-2">
          {/* 버전을 바꾸면 그 버전의 원문으로 다시 채웁니다 */}
          <Writer key={criteria?.version ?? 'none'} criteria={criteria} pitchId={pitchId} />
          <Result criteria={criteria} pitchId={pitchId} />
        </div>
      )}
    </div>
  );
}
