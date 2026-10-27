import { useId, useState } from 'react';
import { useParams } from 'react-router';
import { formatDuration } from '@/shared/lib/clock';
import { isPitchMissing, useTakeHistory } from '@/shared/api/takeHistory';
import { ButtonLink } from '@/shared/ui/Button';
import { InfoPage } from '@/shared/ui/InfoPage';
import { Mascot } from '@/shared/ui/Mascot';
import { Card } from '@/shared/ui/Panel';
import { QueryState } from './QueryState';
import { duration, pct } from './reportFormat';
import { SCRIPT_MODE_LABEL, completedTakes, gazeOffEngine, takeDay } from './takeHistoryFormat';

/**
 * P7 Take 비교 — 한 피치의 Take들을 나란히.
 *
 * FE 원칙이 "점수가 아니라 Take 간 변화"라 총점 줄은 없습니다 (CLAUDE.md).
 * 시선 엔진이 다른 Take끼리는 시선 값을 이어 그리지 않습니다 — 같은 사람도 엔진이 다르면 숫자가 달라집니다.
 */

type MetricKey = 'audience_ratio' | 'script_ratio' | 'cpm' | 'filler_count';

const METRICS: {
  key: MetricKey;
  label: string;
  /** 속도는 빠를수록 좋은 게 아니라서 비워 둡니다 */
  hint: string;
  fmt: (v: number) => string;
}[] = [
  { key: 'audience_ratio', label: '청중 응시', hint: '높을수록 좋아요. ', fmt: (v) => pct(v) },
  { key: 'script_ratio', label: '대본 응시', hint: '낮을수록 좋아요. ', fmt: (v) => pct(v) },
  { key: 'cpm', label: '말하기 속도', hint: '', fmt: (v) => `${v}자/분` },
  { key: 'filler_count', label: '군더더기', hint: '낮을수록 좋아요. ', fmt: (v) => `${v}회` },
];

const GAZE: MetricKey[] = ['audience_ratio', 'script_ratio'];

export function ComparisonPage() {
  const { id = '' } = useParams();
  const history = useTakeHistory(id);
  const [metric, setMetric] = useState<MetricKey>('audience_ratio');
  const tabsId = useId();

  if (!history.data)
    return (
      <InfoPage title="Take 비교">
        {isPitchMissing(history.error) ? (
          <Card>
            <div role="alert" className="flex flex-col gap-3">
              <p className="font-bold">피치를 찾을 수 없어요.</p>
              <p className="text-sm text-stone">지워졌거나 열 수 없는 피치예요.</p>
              <div>
                <ButtonLink to="/">내 피치로</ButtonLink>
              </div>
            </div>
          </Card>
        ) : (
          <QueryState query={history} />
        )}
      </InfoPage>
    );

  const rows = completedTakes(history.data.takes);
  const latestEngine = rows[0]?.gaze_engine ?? null;
  const m = METRICS.find((x) => x.key === metric)!;
  // 오래된 Take부터 그립니다
  const series = [...rows].reverse();
  const max = Math.max(...series.map((r) => r[metric] ?? 0), 1);
  const offEngine = rows.filter((r) => r.gaze_engine !== null && gazeOffEngine(r, latestEngine));

  return (
    <InfoPage title="Take 비교">
      {rows[0] && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
          <p className="min-w-0 font-bold [overflow-wrap:anywhere]">{rows[0].pitch_title}</p>
          <p className="tabular text-sm text-stone">Take {rows.length}개</p>
        </div>
      )}

      {rows.length < 2 ? (
        <Card>
          <Mascot who="pico">비교하려면 Take가 2개 이상 필요해요. 한 번 더 연습해 볼까요?</Mascot>
        </Card>
      ) : (
        <>
          <Card
            title="지표 흐름"
            action={
              <div role="tablist" aria-label="비교 지표" className="flex flex-wrap gap-1.5">
                {METRICS.map((x, index) => (
                  <button
                    key={x.key}
                    type="button"
                    role="tab"
                    id={`${tabsId}-${x.key}`}
                    aria-controls={`${tabsId}-panel`}
                    aria-selected={metric === x.key}
                    tabIndex={metric === x.key ? 0 : -1}
                    onKeyDown={(event) => {
                      let next: number;
                      switch (event.key) {
                        case 'ArrowRight':
                          next = (index + 1) % METRICS.length;
                          break;
                        case 'ArrowLeft':
                          next = (index + METRICS.length - 1) % METRICS.length;
                          break;
                        case 'Home':
                          next = 0;
                          break;
                        case 'End':
                          next = METRICS.length - 1;
                          break;
                        default:
                          return;
                      }
                      event.preventDefault();
                      setMetric(METRICS[next]!.key);
                      event.currentTarget.parentElement?.querySelectorAll('button')[next]?.focus();
                    }}
                    onClick={() => setMetric(x.key)}
                    className={[
                      'min-h-11 px-3 py-1 text-sm font-bold',
                      metric === x.key
                        ? 'bg-ink text-panel'
                        : 'bg-cream text-ink/80 hover:text-ink',
                    ].join(' ')}
                  >
                    {x.label}
                  </button>
                ))}
              </div>
            }
          >
            <div
              role="tabpanel"
              id={`${tabsId}-panel`}
              aria-labelledby={`${tabsId}-${metric}`}
              tabIndex={0}
              className="min-w-0 max-w-full overflow-x-auto p-1"
            >
              <div style={{ minWidth: `${series.length * 6}rem` }}>
                <ol
                  className="flex h-48 gap-4 border-b-2 border-ink"
                  aria-label={`${m.label} Take별 값`}
                >
                  {series.map((r) => {
                    const v = r[metric];
                    const off = GAZE.includes(metric) && gazeOffEngine(r, latestEngine);
                    const empty = v === null || off;
                    return (
                      <li
                        key={r.take_id}
                        aria-label={`Take ${r.take_number}: ${empty ? '측정하지 못했거나 시선 엔진이 달라 비교하지 않아요' : m.fmt(v)}`}
                        className="flex min-w-0 flex-1 flex-col items-center gap-2"
                      >
                        <span className="tabular font-mono text-sm font-bold">
                          {empty ? '—' : m.fmt(v)}
                        </span>
                        {/* 막대 높이의 기준이 되는 칸. 숫자 줄을 뺀 나머지 높이입니다 */}
                        <div className="flex w-full flex-1 items-end">
                          <div
                            className={
                              empty
                                ? 'w-full border-2 border-dashed border-line-strong'
                                : 'w-full bg-coral'
                            }
                            style={{ height: `${empty ? 8 : (v / max) * 100}%` }}
                          />
                        </div>
                      </li>
                    );
                  })}
                </ol>
                <ol className="flex gap-4">
                  {series.map((r) => (
                    <li key={r.take_id} className="flex-1 text-center text-xs font-bold">
                      TAKE {r.take_number}
                    </li>
                  ))}
                </ol>
              </div>
            </div>
            <p className="text-xs text-stone">
              {m.hint}점선은 측정하지 못했거나 시선 엔진이 달라 비교하지 않는 Take예요.
            </p>
          </Card>

          <Card title="Take별 기록">
            <div
              role="region"
              aria-label="Take별 기록 표 · 좌우로 스크롤"
              tabIndex={0}
              className="min-w-0 max-w-full overflow-x-auto"
            >
              <table className="w-full min-w-[40rem] text-left text-sm">
                <thead className="border-b-2 border-ink text-xs text-stone">
                  <tr>
                    <th scope="col" className="py-2 pr-4 font-bold">
                      Take
                    </th>
                    <th className="py-2 pr-4 font-bold">대본 보기</th>
                    <th className="py-2 pr-4 font-bold">시간</th>
                    {METRICS.map((x) => (
                      <th key={x.key} className="py-2 pr-4 font-bold">
                        {x.label}
                      </th>
                    ))}
                    <th className="py-2 font-bold">미션</th>
                  </tr>
                </thead>
                <tbody className="tabular">
                  {rows.map((r) => {
                    const off = gazeOffEngine(r, latestEngine);
                    return (
                      <tr key={r.take_id} className="border-b border-line last:border-b-0">
                        <td className="py-3 pr-4 font-bold">
                          TAKE {r.take_number}{' '}
                          <span className="font-normal text-stone">{takeDay(r.started_at)}</span>
                        </td>
                        <td className="py-3 pr-4">{SCRIPT_MODE_LABEL[r.script_mode]}</td>
                        <td
                          className={r.duration_ms === null ? 'py-3 pr-4 text-stone' : 'py-3 pr-4'}
                        >
                          {duration(r.duration_ms)}
                          <span className="text-stone"> / {formatDuration(r.goal_ms)}</span>
                        </td>
                        {METRICS.map((x) => {
                          const v = r[x.key];
                          const hide = GAZE.includes(x.key) && off;
                          return (
                            <td
                              key={x.key}
                              className="py-3 pr-4"
                              title={hide ? '시선 엔진이 달라 비교하지 않아요' : undefined}
                            >
                              {v === null || hide ? (
                                <span className="text-stone">—</span>
                              ) : (
                                x.fmt(v)
                              )}
                            </td>
                          );
                        })}
                        <td className="py-3">
                          {r.missions_total === 0
                            ? '—'
                            : `${r.missions_achieved} / ${r.missions_total}`}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {offEngine.length > 0 && (
              <p className="text-xs text-stone">
                {offEngine.map((r) => `TAKE ${r.take_number}`).join(' · ')}는 시선 엔진이 지금과
                달라서 시선 값을 비교하지 않아요. 속도 · 군더더기는 비교할 수 있어요.
              </p>
            )}
          </Card>
        </>
      )}
    </InfoPage>
  );
}
