import { useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { formatDuration } from '@/shared/lib/clock';
import { useTakeHistory } from '@/shared/api/takeHistory';
import { takeStatusView } from '@/shared/lib/takeStatus';
import { TakeStatusBadge } from '@/shared/ui/TakeStatusBadge';
import { ButtonLink } from '@/shared/ui/Button';
import { InfoPage } from '@/shared/ui/InfoPage';
import { Mascot } from '@/shared/ui/Mascot';
import { Card } from '@/shared/ui/Panel';
import { QueryState } from './QueryState';
import { duration, pct } from './reportFormat';
import { SCRIPT_MODE_LABEL, takeDay } from './takeHistoryFormat';

/**
 * P16 Take 기록 — 모든 피치의 연습 기록을 최신순으로.
 * 홈은 피치 목록만 두고 이리로 보냅니다(?pitch= 로 그 피치를 골라서). 여기는 "언제 무엇을 연습했나"를 한 줄씩 봅니다.
 */

const ALL = '';

export function TakesPage() {
  const history = useTakeHistory();
  // 홈의 'Take 기록' 링크가 ?pitch= 로 그 피치를 골라서 엽니다
  const [params] = useSearchParams();
  const [pitch, setPitch] = useState(params.get('pitch') ?? ALL);
  const rows = history.data?.takes;

  if (!rows)
    return (
      <InfoPage title="Take 기록" logo={false}>
        <QueryState query={history} />
      </InfoPage>
    );

  // 제목은 겹칠 수 있어 pitch_id 로 거르고, 제목은 보여 주는 데만 씁니다
  const pitches = new Map(rows.map((t) => [t.pitch_id, t.pitch_title]));
  const shown = pitch === ALL ? rows : rows.filter((t) => t.pitch_id === pitch);

  return (
    <InfoPage title="Take 기록" logo={false}>
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
            <label className="flex min-w-0 max-w-full flex-wrap items-center gap-2 text-sm font-bold">
              피치
              <select
                value={pitch}
                onChange={(e) => setPitch(e.target.value)}
                className="min-h-11 min-w-0 max-w-full border-2 border-ink bg-white px-2 py-1 sm:max-w-xs"
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
          <p role="status" className="sr-only">
            연습 기록 {shown.length}개
          </p>
          <ul className="flex flex-col">
            {shown.map((t) => {
              const status = takeStatusView(t.take_id, t.status, t.end_reason ?? null);
              return (
                <li
                  key={t.take_id}
                  className="grid grid-cols-[minmax(0,1fr)_5rem] items-center gap-x-4 gap-y-3 border-b border-line py-4 last:border-b-0 lg:grid-cols-[minmax(0,1fr)_5rem_24rem_6rem]"
                >
                  <div className="min-w-0">
                    <p className="[overflow-wrap:anywhere] text-xs text-stone">
                      {takeDay(t.started_at)} · {t.pitch_title}
                    </p>
                    <p className="mt-0.5 font-mono font-bold">TAKE {t.take_number}</p>
                  </div>
                  <TakeStatusBadge status={status} />
                  <dl className="tabular col-span-2 grid grid-cols-2 items-center gap-x-4 gap-y-2 text-sm sm:grid-cols-3 lg:col-span-1 lg:grid-cols-[minmax(0,1fr)_8rem_5rem]">
                    <div>
                      <dt className="sr-only">대본 보기</dt>
                      <dd className="text-stone">{SCRIPT_MODE_LABEL[t.script_mode]}</dd>
                    </div>
                    <div>
                      <dt className="sr-only">시간</dt>
                      <dd
                        className={`whitespace-nowrap ${t.duration_ms === null ? 'text-stone' : ''}`}
                      >
                        {duration(t.duration_ms)}
                        <span className="text-stone"> / {formatDuration(t.goal_ms)}</span>
                      </dd>
                    </div>
                    <div>
                      <dt className="sr-only">청중 응시</dt>
                      <dd className="whitespace-nowrap">응시 {pct(t.audience_ratio)}</dd>
                    </div>
                  </dl>
                  <div className="col-span-2 min-h-5 text-right lg:col-span-1">
                    {status.link && (
                      <Link
                        to={status.link.to}
                        aria-label={`${t.pitch_title} Take ${t.take_number} ${status.link.text}`}
                        className="inline-flex min-h-11 items-center text-sm font-bold hover:text-coral-deep"
                      >
                        {status.link.text} →
                      </Link>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
      )}
    </InfoPage>
  );
}
