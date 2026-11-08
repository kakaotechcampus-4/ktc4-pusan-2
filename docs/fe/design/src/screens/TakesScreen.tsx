// 제품 Take 기록 + Take 비교 통합 화면(#795)의 설계서 샘플.
import { useState } from 'react';
import { Button } from '@/ui/Button';
import { Card } from '@/ui/Panel';
import { Badge } from '@/ui/Badge';
import { Mascot } from '@/ui/Mascot';
import { Frame, QueryNotice, PITCH, type Navigate } from './shared';

type Metric = 'audience' | 'script' | 'speed' | 'filler';
const metrics: { id: Metric; name: string; hint: string }[] = [
  { id: 'audience', name: '청중 응시', hint: '높을수록 좋아요. ' },
  { id: 'script', name: '대본 응시', hint: '낮을수록 좋아요. ' },
  { id: 'speed', name: '말하기 속도', hint: '' },
  { id: 'filler', name: '군더더기', hint: '낮을수록 좋아요. ' },
];
const samples = [
  {
    take: 1,
    mode: '전체 대본 + 하이라이트',
    time: '05:12',
    audience: null as number | null,
    script: null as number | null,
    speed: 290,
    filler: 8,
  },
  {
    take: 2,
    mode: '핵심 키워드',
    time: '04:38',
    audience: 68 as number | null,
    script: 15 as number | null,
    speed: 310,
    filler: 4,
  },
];

function display(value: number | null, metric: Metric) {
  if (value === null) return '—';
  if (metric === 'speed') return value + '자/분';
  if (metric === 'filler') return value + '회';
  return value + '%';
}

export function TakesScreen({ stateId, onNavigate }: { stateId: string; onNavigate: Navigate }) {
  const comparing = ['compare', 'compare-empty', 'compare-zero'].includes(stateId);
  const [metric, setMetric] = useState<Metric>('audience');
  const selected = metrics.find((item) => item.id === metric)!;
  let count = 2;
  if (stateId === 'compare-zero') count = 0;
  else if (stateId === 'compare-empty') count = 1;

  return (
    <Frame title="Take 기록">
      <QueryNotice stateId={stateId} onRetry={() => onNavigate('takes', 'default')} />
      {stateId !== 'loading' && stateId !== 'error' && (
        <div className="flex min-w-0 flex-col justify-between gap-4 sm:flex-row sm:items-center">
          <nav
            aria-label="Take 기록 보기"
            className="flex w-fit max-w-full flex-wrap border-2 border-ink"
          >
            {[
              { id: 'default', label: '기록 목록', active: !comparing },
              { id: 'compare', label: '지표 비교', active: comparing },
            ].map((item) => (
              <button
                key={item.id}
                type="button"
                aria-current={item.active ? 'page' : undefined}
                onClick={() => onNavigate('takes', item.id)}
                className={
                  'inline-flex min-h-11 items-center justify-center px-4 py-2 text-sm font-bold ' +
                  (item.active ? 'bg-ink text-white' : 'bg-panel text-ink hover:bg-cream')
                }
              >
                {item.label}
              </button>
            ))}
          </nav>
          <label className="flex min-w-0 flex-wrap items-center gap-2 text-sm font-bold">
            피치
            <select
              aria-label="피치"
              defaultValue={comparing ? 'p1' : ''}
              className="min-h-11 min-w-0 max-w-full border-2 border-ink bg-white px-2 py-1 sm:max-w-xs"
            >
              {!comparing && <option value="">전체</option>}
              <option value="p1">{PITCH}</option>
            </select>
          </label>
        </div>
      )}

      {stateId === 'empty' && (
        <Card>
          <Mascot who="chichi">아직 연습 기록이 없어. 첫 Take를 시작해 보자!</Mascot>
          <div>
            <Button onClick={() => onNavigate('home')}>내 피치로</Button>
          </div>
        </Card>
      )}
      {stateId === 'default' && (
        <Card title="최근 연습">
          <ul className="flex flex-col">
            {[...samples].reverse().map((t) => (
              <li
                key={t.take}
                className="grid grid-cols-[minmax(0,1fr)_5rem] items-center gap-x-4 gap-y-3 border-b border-line py-4 last:border-b-0 lg:grid-cols-[minmax(0,1fr)_5rem_24rem_6rem]"
              >
                <div className="min-w-0">
                  <p className="text-xs text-stone">10.28 · {PITCH}</p>
                  <p className="mt-0.5 font-mono font-bold">TAKE {t.take}</p>
                </div>
                <Badge tone="plain">완료</Badge>
                <p className="tabular col-span-2 flex flex-wrap gap-x-4 text-sm lg:col-span-1">
                  <span className="text-stone">{t.mode}</span>
                  <span>
                    {t.time}
                    <span className="text-stone"> / 05:00</span>
                  </span>
                  <span>응시 {display(t.audience, 'audience')}</span>
                </p>
                <div className="col-span-2 text-right lg:col-span-1">
                  <button
                    type="button"
                    onClick={() => onNavigate('report')}
                    className="inline-flex min-h-11 items-center text-sm font-bold hover:text-coral-deep"
                  >
                    리포트 →
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {comparing && (
        <section aria-label="지표 비교" className="flex min-w-0 flex-col gap-5">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
            <p className="min-w-0 font-bold">{PITCH}</p>
            <p className="tabular text-sm text-stone">Take {count}개</p>
          </div>
          {count < 2 ? (
            <Card>
              <Mascot who="pico">
                비교하려면 완료된 Take가 2개 이상 필요해요. 한 번 더 연습해 볼까요?
              </Mascot>
              {count === 1 && (
                <button
                  type="button"
                  onClick={() => onNavigate('report')}
                  className="inline-flex min-h-11 w-fit items-center border-2 border-ink bg-panel px-4 py-2 text-sm font-bold hover:bg-cream"
                >
                  Take 1 리포트 보기 →
                </button>
              )}
            </Card>
          ) : (
            <>
              <Card title="지표 흐름">
                <div role="tablist" aria-label="비교 지표" className="mb-4 flex flex-wrap gap-1.5">
                  {metrics.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      role="tab"
                      aria-selected={metric === item.id}
                      onClick={() => setMetric(item.id)}
                      className={
                        'min-h-11 px-3 py-1 text-sm font-bold ' +
                        (metric === item.id
                          ? 'bg-ink text-panel'
                          : 'bg-cream text-ink/80 hover:text-ink')
                      }
                    >
                      {item.name}
                    </button>
                  ))}
                </div>
                <div
                  role="region"
                  aria-label={selected.name + ' 지표 흐름 · 좌우로 스크롤'}
                  tabIndex={0}
                  className="min-w-0 max-w-full overflow-x-auto p-1"
                >
                  <div
                    className="mx-auto"
                    style={{
                      minWidth: `${samples.length * 6}rem`,
                      width: `min(100%, ${Math.max(samples.length * 8, 24)}rem)`,
                    }}
                  >
                    <ol className="flex h-48 gap-4 border-b-2 border-ink">
                      {samples.map((take) => {
                        const value = take[metric];
                        const max = Math.max(...samples.map((t) => t[metric] ?? 0), 1);
                        return (
                          <li
                            key={take.take}
                            className="flex min-w-0 flex-1 flex-col items-center gap-2"
                          >
                            <span className="tabular font-mono text-sm font-bold">
                              {display(value, metric)}
                            </span>
                            <div className="flex w-full flex-1 items-end justify-center">
                              <div
                                className={
                                  value === null
                                    ? 'w-full max-w-12 border-2 border-dashed border-line-strong'
                                    : 'w-full max-w-12 bg-coral'
                                }
                                style={{
                                  height: value === null ? '8%' : (value / max) * 100 + '%',
                                }}
                              />
                            </div>
                          </li>
                        );
                      })}
                    </ol>
                    <ol className="flex gap-4 text-center text-xs font-bold">
                      {samples.map((take) => (
                        <li key={take.take} className="flex-1">
                          TAKE {take.take}
                        </li>
                      ))}
                    </ol>
                  </div>
                </div>
                <p className="text-xs text-stone">
                  {selected.hint}점선은 측정하지 못했거나 시선 엔진이 달라 비교하지 않는 Take예요.
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
                        {[
                          'Take',
                          '대본 보기',
                          '시간',
                          '청중 응시',
                          '대본 응시',
                          '말하기 속도',
                          '군더더기',
                          '미션',
                          '리포트',
                        ].map((label) => (
                          <th scope="col" key={label} className="py-2 pr-4 font-bold">
                            {label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="tabular">
                      {[...samples].reverse().map((take) => (
                        <tr key={take.take} className="border-b border-line last:border-b-0">
                          <td className="py-3 pr-4 font-bold">TAKE {take.take}</td>
                          <td className="py-3 pr-4">{take.mode}</td>
                          <td className="py-3 pr-4">{take.time} / 05:00</td>
                          {metrics.map((item) => (
                            <td key={item.id} className="py-3 pr-4">
                              {display(take[item.id], item.id)}
                            </td>
                          ))}
                          <td className="py-3 pr-4">—</td>
                          <td className="py-3">
                            <button
                              type="button"
                              onClick={() => onNavigate('report')}
                              aria-label={`Take ${take.take} 리포트 보기`}
                              className="inline-flex min-h-11 items-center whitespace-nowrap font-bold underline underline-offset-4 hover:text-coral-deep"
                            >
                              리포트 보기 →
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </>
          )}
        </section>
      )}
    </Frame>
  );
}
