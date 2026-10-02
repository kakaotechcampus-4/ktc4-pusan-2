import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { parseStandards } from '@/shared/api/standards';
import { useCreateStore } from '../createStore';
import { MAX_CRITERIA, SCORING_LABEL, type CriteriaVersion, type ScoringMode } from '../lib/draft';
import { TimeLimitRow } from './TimeLimitRow';

/**
 * 목업 08 — 평가기준. 00 발표시간 + 최대 5개.
 *
 * 기준은 **자연어로 한 번에 적고**, 서버가 항목으로 나눠 돌려줍니다
 * (`POST /pitches/add/{id}/standards`). 나눈 결과는 아래 목록에 그대로 펼쳐지고,
 * 거기서 한 줄씩 고치거나 지울 수 있습니다 — 서버가 잘못 나눴을 때 다시 적게 하지 않으려고.
 *
 * ★ 채점 방식(`자동 채점` · `발화 대조`)은 목업에만 있습니다. 서버의 `StandardDTO` 는
 *   `{ standard }` 문장 하나뿐이라, 나눠 받은 항목은 모두 `자동 채점` 으로 시작합니다.
 */

const SCORING_ORDER: ScoringMode[] = ['AUTO', 'TRANSCRIPT'];

const PLACEHOLDER = [
  '발표에서 꼭 지키고 싶은 것을 편하게 적어 주세요.',
  '예) 시장 규모 숫자는 반드시 말하고, 도입부 30초는 대본을 보지 않고 말하고 싶어요.',
  '    "음", "어" 같은 군더더기는 5번 이하로 줄일래요.',
].join('\n');

function parseLabel(pending: boolean, hasItems: boolean): string {
  if (pending) return '정리하는 중…';
  if (hasItems) return '다시 정리하기';
  return '기준 정리하기';
}

function NaturalInput({ criteria }: { criteria: CriteriaVersion | null }) {
  const applyParsedCriteria = useCreateStore((s) => s.applyParsedCriteria);
  // ★ 피치 생성 API 가 아직 없어 임시 id 로 보냅니다 — 슬라이드 업로드와 같은 사정입니다
  const pitchId = useCreateStore((s) => s.pitchId ?? s.draftId);
  const [text, setText] = useState(criteria?.sourceText ?? '');

  const parse = useMutation({
    mutationFn: (source: string) => parseStandards(pitchId, source),
    // 결과는 버전에 담습니다. 처음 정리하면 버전이 새로 생기며 이 칸이 다시 그려지는데,
    // 응답을 여기(useMutation)에만 두면 그 순간 "넣지 못한 내용"이 사라집니다
    onSuccess: (res, source) =>
      applyParsedCriteria(criteria?.version ?? null, {
        sourceText: source,
        standards: res.standards.map((s) => s.standard),
        exceptText: res.except_standard,
      }),
  });

  const hasItems = (criteria?.items.length ?? 0) > 0;
  const canParse = text.trim() !== '' && !parse.isPending;
  const except = criteria?.exceptText ?? null;

  return (
    <div className="rounded border border-line-strong bg-white p-3">
      <label htmlFor="criteria-source" className="text-xs font-bold">
        어떤 점을 평가받고 싶나요?
      </label>
      <textarea
        id="criteria-source"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={PLACEHOLDER}
        rows={4}
        className="mt-2 w-full resize-y rounded border border-line bg-panel/40 p-3 text-sm leading-relaxed outline-none placeholder:text-stone focus:border-ink"
      />

      <div className="mt-2 flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={!canParse}
          onClick={() => parse.mutate(text)}
          className="inline-flex items-center gap-2 rounded bg-coral px-4 py-2 text-xs font-bold text-white hover:bg-coral-deep disabled:cursor-not-allowed disabled:bg-line disabled:text-stone"
        >
          {parseLabel(parse.isPending, hasItems)}
        </button>
        <span className="text-xs text-stone">
          {hasItems
            ? '다시 정리하면 아래 목록이 새로 바뀌어요'
            : `최대 ${MAX_CRITERIA}개의 기준으로 나눠 드려요`}
        </span>
      </div>

      {parse.isError && (
        <p role="alert" className="mt-2 text-xs font-bold text-coral">
          기준을 정리하지 못했어요. 잠시 뒤 다시 시도해 주세요.
        </p>
      )}

      {/* 5개를 넘었거나 기준으로 삼기 어려운 문장. 버리지 않고 보여 줍니다 — 몰래 빠지면 왜 없는지 모릅니다 */}
      {except && (
        <p className="mt-2 rounded bg-cream px-3 py-2 text-xs text-stone">
          <span className="font-bold text-ink">기준으로 넣지 못한 내용</span> · {except}
        </p>
      )}
    </div>
  );
}

function CriteriaRows({ criteria }: { criteria: CriteriaVersion }) {
  const addCriterion = useCreateStore((s) => s.addCriterion);
  const editCriterion = useCreateStore((s) => s.editCriterion);
  const removeCriterion = useCreateStore((s) => s.removeCriterion);

  const rows = Array.from({ length: MAX_CRITERIA }, (_, i) => criteria.items[i] ?? null);

  return (
    <ul className="flex flex-col gap-3">
      {rows.map((item, i) => {
        const no = String(i + 1).padStart(2, '0');

        if (item === null) {
          return (
            <li key={`empty-${i}`}>
              <button
                type="button"
                onClick={() => addCriterion(criteria.version)}
                className="flex w-full items-stretch gap-0 text-left"
              >
                <span className="tabular flex w-14 shrink-0 items-center justify-center rounded-l border border-dashed border-line-strong font-mono text-xs text-stone">
                  {no}
                </span>
                <span className="flex-1 rounded-r border border-dashed border-line-strong px-4 py-3 text-sm text-stone">
                  + 평가기준 추가
                </span>
              </button>
            </li>
          );
        }

        return (
          <li key={item.id} className="flex items-stretch gap-0">
            <span className="tabular flex w-14 shrink-0 items-center justify-center rounded-l bg-ink font-mono text-xs font-bold text-panel">
              {no}
            </span>
            <div className="flex flex-1 items-center gap-3 rounded-r border border-line bg-panel px-4 py-2">
              <input
                aria-label={`평가기준 ${no}`}
                value={item.text}
                onChange={(e) => editCriterion(criteria.version, item.id, { text: e.target.value })}
                placeholder="발표에서 반드시 지킬 것을 한 줄로"
                className="flex-1 bg-transparent text-sm outline-none"
              />

              {/* 채점 방식 — 두 개뿐이라 토글로 둡니다 */}
              <button
                type="button"
                onClick={() =>
                  editCriterion(criteria.version, item.id, {
                    scoring: SCORING_ORDER[(SCORING_ORDER.indexOf(item.scoring) + 1) % 2] ?? 'AUTO',
                  })
                }
                className="shrink-0 rounded border border-line px-2 py-1 text-xs text-stone hover:text-ink"
              >
                {SCORING_LABEL[item.scoring]}
              </button>

              <button
                type="button"
                onClick={() => removeCriterion(criteria.version, item.id)}
                aria-label={`평가기준 ${no} 삭제`}
                className="shrink-0 px-1 text-stone hover:text-coral"
              >
                ✕
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function CriteriaPane({ criteria }: { criteria: CriteriaVersion | null }) {
  return (
    <div className="flex flex-1 flex-col">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-lg font-bold">평가기준을 입력해주세요</h2>
        {criteria && (
          <span className="tabular rounded border border-line-strong bg-cream px-3 py-2 font-mono text-xs font-bold">
            {criteria.items.length} / {MAX_CRITERIA} 입력됨
          </span>
        )}
      </div>

      <div className="flex flex-1 flex-col gap-3 rounded border-2 border-ink bg-panel p-4">
        {/* 평가기준을 안 만들어도 발표시간(00)은 정합니다 — 기준은 선택이지만 시간은 늘 잽니다 */}
        <TimeLimitRow />

        {/* 버전을 바꾸면 그 버전의 원문으로 다시 채웁니다 */}
        <NaturalInput key={criteria?.version ?? 'none'} criteria={criteria} />

        {/* 사이드바에서 빈 버전을 만든 경우에도 목록을 보입니다 — 손으로 한 줄씩 넣을 수 있게 */}
        {criteria ? (
          <CriteriaRows criteria={criteria} />
        ) : (
          <p className="rounded border-2 border-dashed border-line-strong py-8 text-center text-sm text-stone">
            정리한 기준이 여기에 01부터 차례로 나와요 · 평가기준은 선택 사항이에요
          </p>
        )}

        <p className="mt-auto pt-3 text-xs text-stone">
          기준은 최대 {MAX_CRITERIA}개까지. 적을수록 피드백이 선명해져요.
        </p>
      </div>
    </div>
  );
}
