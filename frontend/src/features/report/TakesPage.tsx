import { useState } from 'react';
import { Link } from 'react-router';
import { formatDuration } from '@/shared/lib/clock';
import { useTakeHistory } from '@/shared/api/takeHistory';
import { Badge, type Tone } from '@/shared/ui/Badge';
import { ButtonLink } from '@/shared/ui/Button';
import { InfoPage } from '@/shared/ui/InfoPage';
import { Mascot } from '@/shared/ui/Mascot';
import { Card } from '@/shared/ui/Panel';
import type { TakeStatus } from '@/types/api';
import { QueryState } from './QueryState';
import { pct } from './reportFormat';
import { SCRIPT_MODE_LABEL, takeDay } from './takeHistoryFormat';

/**
 * P16 Take 기록 — 모든 피치의 연습 기록을 최신순으로.
 * 홈은 피치별로 접어 보여 주고, 여기는 "언제 무엇을 연습했나"를 한 줄씩 봅니다.
 */

const ALL = '';

const STATUS: Record<TakeStatus, { label: string; tone: Tone; link: string | null }> = {
  READY: { label: '준비 중', tone: 'muted', link: null },
  RUNNING: { label: '연습 중', tone: 'good', link: null },
  ANALYZING: { label: '분석 중', tone: 'good', link: '진행 보기 →' },
  COMPLETED: { label: '완료', tone: 'plain', link: '리포트 →' },
  FAILED: { label: '분석 실패', tone: 'muted', link: null },
};

export function TakesPage() {
  const history = useTakeHistory();
  const [pitch, setPitch] = useState(ALL);
  const rows = history.data?.takes;

  if (!rows)
    return (
      <InfoPage title="Take 기록">
        <QueryState query={history} />
      </InfoPage>
    );

  // 제목은 겹칠 수 있어 pitch_id 로 거르고, 제목은 보여 주는 데만 씁니다
  const pitches = new Map(rows.map((t) => [t.pitch_id, t.pitch_title]));
  const shown = pitch === ALL ? rows : rows.filter((t) => t.pitch_id === pitch);

  return (
    <InfoPage title="Take 기록">
      {rows.length === 0 ? (
        <Card>
          <Mascot who="chichi">아직 연습 기록이 없어. 첫 Take를 시작해 보자!</Mascot>
          <div>
            <ButtonLink to="/">내 피치로</ButtonLink>
          </div>
        </Card>
      ) : (
        <Card
          title="최근 연습"
          action={
            <label className="flex items-center gap-2 text-sm font-bold">
              피치
              <select
                value={pitch}
                onChange={(e) => setPitch(e.target.value)}
                className="border-2 border-ink bg-white px-2 py-1"
              >
                <option value={ALL}>전체</option>
                {[...pitches].map(([id, title]) => (
                  <option key={id} value={id}>
                    {title}
                  </option>
                ))}
              </select>
            </label>
          }
        >
          <ul className="flex flex-col">
            {shown.map((t) => {
              const status = STATUS[t.status];
              return (
                <li
                  key={t.take_id}
                  className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-line py-4 last:border-b-0"
                >
                  <div className="min-w-0">
                    <p className="text-xs text-stone">
                      {takeDay(t.started_at)} · {t.pitch_title}
                    </p>
                    <p className="mt-0.5 flex items-center gap-2 font-bold">
                      TAKE {t.take_number}
                      <Badge tone={status.tone}>{status.label}</Badge>
                    </p>
                  </div>
                  <dl className="tabular flex flex-wrap gap-x-6 gap-y-1 text-sm">
                    <div>
                      <dt className="sr-only">대본 보기</dt>
                      <dd className="text-stone">{SCRIPT_MODE_LABEL[t.script_mode]}</dd>
                    </div>
                    <div>
                      <dt className="sr-only">시간</dt>
                      <dd>
                        {t.duration_ms === null ? '—' : formatDuration(t.duration_ms)}
                        <span className="text-stone"> / {formatDuration(t.goal_ms)}</span>
                      </dd>
                    </div>
                    <div>
                      <dt className="sr-only">청중 응시</dt>
                      <dd>응시 {pct(t.audience_ratio)}</dd>
                    </div>
                  </dl>
                  {status.link ? (
                    <Link
                      to={
                        t.status === 'ANALYZING'
                          ? `/takes/${t.take_id}/processing`
                          : `/takes/${t.take_id}`
                      }
                      className="text-sm font-bold hover:text-coral-deep"
                    >
                      {status.link}
                    </Link>
                  ) : (
                    <span />
                  )}
                </li>
              );
            })}
          </ul>
        </Card>
      )}
    </InfoPage>
  );
}
