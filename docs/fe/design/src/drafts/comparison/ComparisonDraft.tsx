import { useState } from 'react';
import { formatDuration } from '@/lib/clock';
import { Card } from '@/ui/Panel';
import { DraftPage, Mascot } from '../ui';
import { SCRIPT_MODE_LABEL, pct, takes, type TakeRow } from '../takesSample';

/**
 * Take 비교 시안 (P7) — 한 피치의 Take들을 나란히.
 *
 * FE 원칙이 "점수가 아니라 Take 간 변화"라 총점 줄은 없습니다 (CLAUDE.md).
 * 시선 엔진이 다른 Take끼리는 시선 값을 이어 그리지 않습니다 — 같은 사람도 엔진이 다르면 숫자가 달라집니다.
 */

type Variant = 'OK' | 'ONE' | 'ENGINE';

const VARIANTS = [
  { id: 'OK' as const, label: '정상' },
  { id: 'ONE' as const, label: 'Take 1개' },
  { id: 'ENGINE' as const, label: '시선 엔진 다름 포함' },
];

const ROWS: Record<Variant, TakeRow[]> = {
  OK: takes.slice(0, 2),
  ONE: takes.slice(0, 1),
  ENGINE: takes,
};

type MetricKey = 'audience_ratio' | 'script_ratio' | 'cpm' | 'filler_count';

const METRICS: {
  key: MetricKey;
  label: string;
  better: 'up' | 'down';
  fmt: (v: number) => string;
}[] = [
  { key: 'audience_ratio', label: '청중 응시', better: 'up', fmt: (v) => pct(v) },
  { key: 'script_ratio', label: '대본 응시', better: 'down', fmt: (v) => pct(v) },
  { key: 'cpm', label: '말하기 속도', better: 'up', fmt: (v) => `${v}자/분` },
  { key: 'filler_count', label: '군더더기', better: 'down', fmt: (v) => `${v}회` },
];

const GAZE: MetricKey[] = ['audience_ratio', 'script_ratio'];

export function ComparisonDraft() {
  const [variant, setVariant] = useState<Variant>('OK');
  const [metric, setMetric] = useState<MetricKey>('audience_ratio');
  const rows = ROWS[variant];
  const latestEngine = rows[0]!.gaze_engine;
  const m = METRICS.find((x) => x.key === metric)!;
  // 오래된 Take부터 그립니다
  const series = [...rows].reverse();
  const max = Math.max(...series.map((r) => r[metric] ?? 0), 1);

  return (
    <DraftPage
      title="Take 비교"
      eyebrow="캡스톤 중간발표"
      meta={`Take ${rows.length}개`}
      variants={VARIANTS}
      variant={variant}
      onVariant={setVariant}
      questions={[
        '속도는 빠를수록 좋은 게 아니라 목표 범위에 가까울수록 좋아요. AI 리뷰의 비교 규칙(review-agent.md 6-1)을 그대로 쓸지 정해야 해요.',
        '여기에 쓸 Take 목록 API가 없어요. BE와 정해야 해요.',
        '대본 보기가 다른 Take끼리 비교할 때 경고를 어디까지 보여 줄지 정해야 해요.',
      ]}
    >
      {rows.length < 2 ? (
        <Card>
          <Mascot who="pico">비교하려면 Take가 2개 이상 필요해요. 한 번 더 연습해 볼까요?</Mascot>
        </Card>
      ) : (
        <>
          <Card
            title="지표 흐름"
            action={
              <div role="tablist" className="flex flex-wrap gap-1.5">
                {METRICS.map((x) => (
                  <button
                    key={x.key}
                    type="button"
                    role="tab"
                    aria-selected={metric === x.key}
                    onClick={() => setMetric(x.key)}
                    className={[
                      'px-3 py-1 text-sm font-bold',
                      metric === x.key ? 'bg-ink text-panel' : 'bg-cream text-stone hover:text-ink',
                    ].join(' ')}
                  >
                    {x.label}
                  </button>
                ))}
              </div>
            }
          >
            <ol className="flex h-48 gap-4 border-b-2 border-ink">
              {series.map((r) => {
                const v = r[metric];
                const off = GAZE.includes(metric) && r.gaze_engine !== latestEngine;
                return (
                  <li key={r.take_id} className="flex flex-1 flex-col items-center gap-2">
                    <span className="tabular font-mono text-sm font-bold">
                      {v === null || off ? '—' : m.fmt(v)}
                    </span>
                    {/* 막대 높이의 기준이 되는 칸. 숫자 줄을 뺀 나머지 높이입니다 */}
                    <div className="flex w-full flex-1 items-end">
                      <div
                        className={
                          off || v === null
                            ? 'w-full border-2 border-dashed border-line-strong'
                            : 'w-full bg-coral'
                        }
                        style={{ height: `${v === null || off ? 8 : (v / max) * 100}%` }}
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
            <p className="text-xs text-stone">
              {m.better === 'up' ? '높을수록 좋아요.' : '낮을수록 좋아요.'} 점선은 측정하지 못했거나
              시선 엔진이 달라 비교하지 않는 Take예요.
            </p>
          </Card>

          <Card title="Take별 기록">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[40rem] text-left text-sm">
                <thead className="border-b-2 border-ink text-xs text-stone">
                  <tr>
                    <th className="py-2 pr-4 font-bold">Take</th>
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
                    const off = r.gaze_engine !== latestEngine;
                    return (
                      <tr key={r.take_id} className="border-b border-line last:border-b-0">
                        <td className="py-3 pr-4 font-bold">
                          TAKE {r.take_number}{' '}
                          <span className="font-normal text-stone">{r.date}</span>
                        </td>
                        <td className="py-3 pr-4">{SCRIPT_MODE_LABEL[r.script_mode]}</td>
                        <td className="py-3 pr-4">
                          {formatDuration(r.duration_ms)}
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
            {variant === 'ENGINE' && (
              <p className="text-xs text-stone">
                TAKE 1 · 2는 시선 엔진이 지금과 달라서 시선 값을 비교하지 않아요. 속도 · 군더더기는
                비교할 수 있어요.
              </p>
            )}
          </Card>
        </>
      )}
    </DraftPage>
  );
}
