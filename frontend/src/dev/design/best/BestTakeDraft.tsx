import { useState } from 'react';
import { formatDuration } from '@/shared/lib/clock';
import { Badge } from '@/shared/ui/Badge';
import { Card } from '@/shared/ui/Panel';
import { Button } from '@/shared/ui/Button';
import { DraftPage } from '../ui';
import { SCRIPT_MODE_LABEL, pct, takes } from '../takesSample';

/**
 * Best Take 선택 시안 (P8) — 이 피치에서 "이게 내 최선"인 Take를 하나 고릅니다.
 *
 * 추천은 할 수 있지만 고르는 건 사용자입니다. 추천 이유는 숫자로 보여 주고, 총점으로 줄 세우지 않습니다.
 */

type Variant = 'PICK' | 'CHOSEN';

const VARIANTS = [
  { id: 'PICK' as const, label: '처음 고르기' },
  { id: 'CHOSEN' as const, label: '이미 고름' },
];

const RECOMMENDED = 't4';
const REASONS = [
  '청중 응시가 가장 높아요 (74%)',
  '목표 시간 안에 끝냈어요',
  '군더더기가 가장 적어요 (7회)',
];

export function BestTakeDraft() {
  const [variant, setVariant] = useState<Variant>('PICK');
  const current = takes.find((t) => t.is_best)!.take_id;
  const [picked, setPicked] = useState<string | null>(null);
  const selected = picked ?? (variant === 'CHOSEN' ? current : null);
  const done = takes.filter((t) => t.status === 'COMPLETED');

  return (
    <DraftPage
      title="Best Take 선택"
      eyebrow="캡스톤 중간발표"
      variants={VARIANTS}
      variant={variant}
      onVariant={(v) => {
        setVariant(v);
        setPicked(null);
      }}
      questions={[
        'Best Take가 어디에 쓰이는지 정해야 해요 (홈 대표 기록 · 비교 기준 · 실전 전 마지막 확인).',
        '추천을 FE 규칙으로 할지, AI · BE가 줄지 정해야 해요. 지금 AI 계약에는 추천이 없어요.',
      ]}
    >
      <Card>
        <p className="text-stone">
          이 피치에서 가장 마음에 드는 Take를 하나 골라 주세요. 언제든 바꿀 수 있어요.
        </p>
      </Card>

      <fieldset className="grid gap-4 md:grid-cols-2">
        <legend className="sr-only">Best Take</legend>
        {done.map((t) => {
          const on = selected === t.take_id;
          return (
            <label
              key={t.take_id}
              className={[
                'flex cursor-pointer flex-col gap-3 border-2 border-ink p-5 shadow-[4px_4px_0_var(--color-line)]',
                on ? 'bg-coral-wash' : 'bg-panel hover:bg-cream',
              ].join(' ')}
            >
              <div className="flex items-center justify-between gap-3">
                <span className="flex items-center gap-3">
                  <input
                    type="radio"
                    name="best"
                    checked={on}
                    onChange={() => setPicked(t.take_id)}
                    className="h-4 w-4 accent-coral"
                  />
                  <span className="text-lg font-bold">TAKE {t.take_number}</span>
                  <span className="text-sm text-stone">{t.date}</span>
                </span>
                <span className="flex gap-1.5">
                  {t.take_id === RECOMMENDED && <Badge tone="bad">추천</Badge>}
                  {t.is_best && variant === 'CHOSEN' && <Badge tone="strong">지금 BEST</Badge>}
                </span>
              </div>
              <dl className="tabular grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
                <div>
                  <dt className="text-xs text-stone">시간</dt>
                  <dd className="font-bold">{formatDuration(t.duration_ms)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-stone">청중 응시</dt>
                  <dd className="font-bold">{pct(t.audience_ratio)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-stone">속도</dt>
                  <dd className="font-bold">{t.cpm === null ? '—' : `${t.cpm}자/분`}</dd>
                </div>
                <div>
                  <dt className="text-xs text-stone">군더더기</dt>
                  <dd className="font-bold">
                    {t.filler_count === null ? '—' : `${t.filler_count}회`}
                  </dd>
                </div>
              </dl>
              <p className="text-xs text-stone">대본 보기 · {SCRIPT_MODE_LABEL[t.script_mode]}</p>
              {t.take_id === RECOMMENDED && (
                <ul className="list-disc space-y-0.5 pl-5 text-sm">
                  {REASONS.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              )}
            </label>
          );
        })}
      </fieldset>

      <div className="flex flex-wrap justify-end gap-3">
        <Button variant="secondary">리포트 보기</Button>
        <Button>
          {selected
            ? `TAKE ${takes.find((t) => t.take_id === selected)!.take_number}을 Best로`
            : 'Take를 골라 주세요'}
        </Button>
      </div>
    </DraftPage>
  );
}
