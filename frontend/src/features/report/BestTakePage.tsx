import { useState } from 'react';
import { useParams } from 'react-router';
import { formatDuration } from '@/shared/lib/clock';
import { useChooseBestTake, useTakeHistory } from '@/shared/api/takeHistory';
import { Badge } from '@/shared/ui/Badge';
import { Button, ButtonLink } from '@/shared/ui/Button';
import { InfoPage } from '@/shared/ui/InfoPage';
import { Mascot } from '@/shared/ui/Mascot';
import { Card } from '@/shared/ui/Panel';
import { QueryState } from './QueryState';
import { SCRIPT_MODE_LABEL, completedTakes, pct, takeDay } from './takeHistoryFormat';

/**
 * P8 Best Take 선택 — 이 피치에서 "이게 내 최선"인 Take를 하나 고릅니다.
 *
 * 고르는 건 사용자입니다. 총점으로 줄 세우지 않습니다.
 * 추천은 아직 넣지 않았습니다 — FE 규칙으로 할지 AI · BE 가 줄지 정해지지 않았습니다 (시안 P8).
 */
export function BestTakePage() {
  const { id = '' } = useParams();
  const history = useTakeHistory(id);
  const choose = useChooseBestTake(id);
  const [picked, setPicked] = useState<string | null>(null);

  if (!history.data)
    return (
      <InfoPage title="Best Take 선택">
        <QueryState query={history} />
      </InfoPage>
    );

  const done = completedTakes(history.data.takes);
  const current = done.find((t) => t.is_best)?.take_id ?? null;
  const selected = picked ?? current;
  const selectedTake = done.find((t) => t.take_id === selected);
  let saveLabel = 'Take를 골라 주세요';
  if (choose.isPending) saveLabel = '저장하는 중…';
  else if (selectedTake) saveLabel = `TAKE ${selectedTake.take_number} Best로 정하기`;

  return (
    <InfoPage title="Best Take 선택">
      {done[0] && (
        <div className="border-b border-line pb-4">
          <p className="font-bold">{done[0].pitch_title}</p>
        </div>
      )}

      {done.length === 0 ? (
        <Card>
          <Mascot who="pico">
            분석이 끝난 Take가 아직 없어요. 리포트가 나오면 여기서 고를 수 있어요.
          </Mascot>
        </Card>
      ) : (
        <>
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
                      <span className="text-sm text-stone">{takeDay(t.started_at)}</span>
                    </span>
                    {t.is_best && <Badge tone="strong">지금 BEST</Badge>}
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
                  <p className="text-xs text-stone">
                    대본 보기 · {SCRIPT_MODE_LABEL[t.script_mode]}
                  </p>
                </label>
              );
            })}
          </fieldset>

          <div className="flex flex-wrap items-center justify-end gap-3">
            <p role="status" className="text-sm">
              {choose.isError && (
                <span className="text-coral-deep">저장하지 못했어요. 다시 시도해 주세요.</span>
              )}
              {choose.isSuccess && selected === current && 'Best Take로 정했어요.'}
            </p>
            {selectedTake && (
              <ButtonLink variant="secondary" to={`/takes/${selectedTake.take_id}`}>
                리포트 보기
              </ButtonLink>
            )}
            <Button
              disabled={!selectedTake || selected === current || choose.isPending}
              onClick={() => selectedTake && choose.mutate({ take_id: selectedTake.take_id })}
            >
              {saveLabel}
            </Button>
          </div>
        </>
      )}
    </InfoPage>
  );
}
