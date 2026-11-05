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
import { TakeComparison } from './ComparisonPage';
import { duration, pct } from './reportFormat';
import { completedTakes, SCRIPT_MODE_LABEL, takeDay } from './takeHistoryFormat';

/** P16: 목록과 비교는 한 페이지, 피치/화면 선택은 URL에 남깁니다 (#795). */
const ALL = '';

export function TakesPage() {
  // 목록은 전체 이력을 한 번만 조회하고, 비교에 넘길 때 반드시 같은 pitch_id로 먼저 거릅니다.
  const history = useTakeHistory();
  const [params, setParams] = useSearchParams();
  const rows = history.data?.takes;

  if (!rows)
    return (
      <InfoPage title="Take 기록" logo={false}>
        <QueryState query={history} />
      </InfoPage>
    );

  const pitches = new Map(rows.map((t) => [t.pitch_id, t.pitch_title]));
  const firstPitch = pitches.keys().next().value ?? ALL;
  const comparing = params.get('view') === 'compare';
  const asked = params.get('pitch') ?? ALL;
  // 목록의 사라진 피치는 전체로 복구하되, 비교 URL의 피치는 다른 발표로 바꾸지 않습니다.
  let pitch = ALL;
  if (comparing) pitch = asked || firstPitch;
  else if (pitches.has(asked)) pitch = asked;
  const shown = rows.filter((t) => (!comparing && pitch === ALL) || t.pitch_id === pitch);
  const compared = completedTakes(shown);

  function viewPath(view: 'list' | 'compare') {
    const next = new URLSearchParams(params);
    if (view === 'compare') {
      next.set('view', 'compare');
      if (pitch || firstPitch) next.set('pitch', pitch || firstPitch);
    } else {
      next.delete('view');
      if (pitch) next.set('pitch', pitch);
      else next.delete('pitch');
    }
    const query = next.toString();
    return '/takes' + (query ? '?' + query : '');
  }

  function selectPitch(selected: string) {
    const next = new URLSearchParams(params);
    if (selected) next.set('pitch', selected);
    else next.delete('pitch');
    // push로 기록하여 뒤로 가기 시 이전 필터를 복원합니다.
    setParams(next);
  }

  const viewLink =
    'inline-flex min-h-11 items-center justify-center px-4 py-2 text-sm font-bold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-coral-deep';

  return (
    <InfoPage title="Take 기록" logo={false}>
      <div className="flex min-w-0 flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <nav
          aria-label="Take 기록 보기"
          className="flex w-fit max-w-full flex-wrap border-2 border-ink"
        >
          <Link
            to={viewPath('list')}
            aria-current={!comparing ? 'page' : undefined}
            className={
              viewLink +
              (!comparing ? ' bg-ink text-white' : ' bg-panel text-ink hover:bg-cream')
            }
          >
            기록 목록
          </Link>
          <Link
            to={viewPath('compare')}
            aria-current={comparing ? 'page' : undefined}
            className={
              viewLink +
              (comparing ? ' bg-ink text-white' : ' bg-panel text-ink hover:bg-cream')
            }
          >
            지표 비교
          </Link>
        </nav>
        <label className="flex min-w-0 max-w-full flex-wrap items-center gap-2 text-sm font-bold">
          피치
          <select
            value={pitch}
            onChange={(event) => selectPitch(event.target.value)}
            className="min-h-11 min-w-0 max-w-full border-2 border-ink bg-white px-2 py-1 sm:max-w-xs"
          >
            {!comparing && <option value={ALL}>전체</option>}
            {comparing && !pitch && <option value={ALL}>선택할 피치가 없어요</option>}
            {comparing && pitch && !pitches.has(pitch) && (
              <option value={pitch}>기록이 없는 피치</option>
            )}
            {[...pitches].map(([id, title]) => (
              <option key={id} value={id}>
                {title}
              </option>
            ))}
          </select>
        </label>
      </div>

      {comparing && (
        <section aria-label="지표 비교" className="flex min-w-0 flex-col gap-5">
          {pitch && !pitches.has(pitch) && (
            <p role="status" className="text-sm text-stone">
              선택한 피치의 기록이 없어요. 다른 피치를 선택하거나 목록으로 돌아가 주세요.
            </p>
          )}
          <TakeComparison rows={compared} />
        </section>
      )}
      {!comparing && rows.length === 0 && (
        <Card>
          <Mascot who="chichi">아직 연습 기록이 없어. 첫 Take를 시작해 보자!</Mascot>
          <div>
            <ButtonLink to="/">내 피치로</ButtonLink>
          </div>
        </Card>
      )}
      {!comparing && rows.length > 0 && (
        <Card title="최근 연습">
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
                        className={
                          'whitespace-nowrap ' + (t.duration_ms === null ? 'text-stone' : '')
                        }
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
                        aria-label={
                          t.pitch_title + ' Take ' + t.take_number + ' ' + status.link.text
                        }
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
