import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { toMessage } from '@/shared/api/errorMessage';
import { parseStandards } from '@/shared/api/standards';
import { useCreateStore } from '../createStore';
import { AlertIcon, CheckIcon, DocIcon, RefreshIcon } from '../icons';
import { fromStandardsPosted } from '../lib/beAdapter';
import type { CriteriaVersion } from '../lib/draft';
import { NeedPitch } from './NeedPitch';
import { PaneHeading } from './PaneHeading';

/**
 * 목업 평가기준 — 왼쪽에 자연어로 한 번에 적고, 오른쪽에서 정리된 결과를 확인해 저장합니다.
 *
 * 나누는 일은 서버가 합니다 (`POST /pitches/add/{id}/standards`). 결과는 고치지 않고 보여만
 * 줍니다 — 고치고 싶으면 왼쪽 글을 고쳐 "다시 정리"합니다. 목록과 원문이 따로 놀지 않게.
 *
 * ★ BE 는 나누면서 **바로 저장**합니다. 그래서 목업의 "평가기준 저장" 단계를 두지 않고,
 *   다시 정리할 때마다 새 버전이 생깁니다 (스토어도 같은 규칙).
 * 발표시간은 여기가 아니라 발표정보에서 정합니다.
 */

/** BE 가 아직 나누지 못해 결과 자리가 비어 온 경우 (`add_pitch_standard_service` 미구현) */
class StandardsNotReady extends Error {}

function parseErrorMessage(error: unknown): string {
  if (error instanceof StandardsNotReady) return '서버가 아직 평가기준을 나누지 못해요.';
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

/** 왼쪽 아래 상자. 서버가 넣지 못한 내용이 있으면 그것을, 없으면 작성 요령을 보여 줍니다 */
function Guide({ except }: { except: string | null }) {
  if (except) {
    return (
      <div role="status" className="rounded-lg border border-coral/40 bg-coral-wash px-4 py-3">
        <p className="flex items-center gap-2 text-sm font-bold text-coral-deep">
          <AlertIcon className="h-4 w-4 fill-coral" />
          다시 확인해 주세요
        </p>
        <p className="mt-1.5 pl-6 text-sm leading-relaxed">
          아래 내용은 평가할 행동이 구체적이지 않아 반영하지 못했어요.
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
  const [text, setText] = useState(criteria?.sourceText ?? '');

  const parse = useMutation({
    mutationFn: async (source: string) => {
      const parsed = fromStandardsPosted(await parseStandards(pitchId, source));
      if (!parsed) throw new StandardsNotReady();
      return parsed;
    },
    // 결과는 버전에 담습니다. 처음 정리하면 버전이 새로 생기며 이 칸이 다시 그려지는데,
    // 응답을 여기(useMutation)에만 두면 그 순간 "넣지 못한 내용"이 사라집니다
    onSuccess: (res, source) =>
      applyParsedCriteria(criteria?.version ?? null, { sourceText: source, ...res }),
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
        onClick={() => parse.mutate(text)}
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
      {except && <p className="text-xs text-stone">위 입력문을 수정한 뒤 다시 정리해 주세요.</p>}
    </section>
  );
}

function Result({ criteria }: { criteria: CriteriaVersion | null }) {
  const items = criteria?.items ?? [];

  return (
    <section className="flex min-w-0 flex-col rounded-lg border border-line bg-white p-4 xl:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2.5">
          <h3 className="text-lg font-bold">정리된 평가기준</h3>
          <span className="tabular rounded-md bg-panel px-2.5 py-1 text-xs font-bold text-stone">
            {items.length}개 항목
          </span>
        </div>
        {criteria?.saved && (
          <span className="flex items-center gap-1.5 rounded-md border border-line-strong bg-panel px-2.5 py-1 text-xs font-bold">
            <CheckIcon />V{criteria.version} 저장됨
          </span>
        )}
      </div>

      {items.length === 0 ? (
        <div className="flex min-h-48 flex-1 flex-col items-center justify-center gap-2 py-6 text-center">
          <DocIcon className="mb-2 h-9 w-9 stroke-stone" />
          <p className="font-bold">아직 정리된 평가기준이 없어요</p>
          <p className="text-sm text-stone">기준을 작성하고 정리 버튼을 눌러 주세요.</p>
        </div>
      ) : (
        <ol className="mt-4 flex flex-1 flex-col gap-2.5">
          {items.map((item, i) => (
            <li
              key={item.id}
              className="flex items-center gap-4 rounded-lg border border-line bg-white px-3 py-3"
            >
              <span className="tabular flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-panel text-sm font-bold">
                {String(i + 1).padStart(2, '0')}
              </span>
              <span className="min-w-0 text-sm [overflow-wrap:anywhere]">{item.text}</span>
            </li>
          ))}
        </ol>
      )}

      <p className="mt-4 border-t border-line pt-4 text-xs text-stone">
        {items.length === 0
          ? '정리하면 바로 저장돼요.'
          : '입력한 기준을 수정해 다시 정리하면 새 버전으로 저장돼요.'}
      </p>
    </section>
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
          <Result criteria={criteria} />
        </div>
      )}
    </div>
  );
}
