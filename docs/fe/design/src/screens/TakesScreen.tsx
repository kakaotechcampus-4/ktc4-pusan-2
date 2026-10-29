// 제품 Take 기록(`frontend/src/features/report/TakesPage.tsx`)의 설계서 샘플.
import { Button } from '@/ui/Button';
import { Card } from '@/ui/Panel';
import { Badge } from '@/ui/Badge';
import { Mascot } from '@/ui/Mascot';
import { Frame, QueryNotice, PITCH, type Navigate } from './shared';

export function TakesScreen({ stateId, onNavigate }: { stateId: string; onNavigate: Navigate }) {
  return (
    <Frame title="Take 기록">
      <QueryNotice stateId={stateId} onRetry={() => onNavigate('takes', 'default')} />
      {stateId === 'empty' && (
        <Card>
          <Mascot who="chichi">아직 연습 기록이 없어. 첫 Take를 시작해 보자!</Mascot>
          <div>
            <Button onClick={() => onNavigate('home')}>내 피치로</Button>
          </div>
        </Card>
      )}
      {stateId === 'default' && (
        <Card
          title="최근 연습"
          action={
            <label className="flex items-center gap-2 text-sm font-bold">
              피치
              <select className="min-h-11 border-2 border-ink bg-white px-2 py-1" defaultValue="">
                <option value="">전체</option>
                <option value="p1">{PITCH}</option>
              </select>
            </label>
          }
        >
          <ul className="flex flex-col">
            {[
              { take: 2, mode: '핵심 키워드', time: '04:38', gaze: '68%' },
              // 측정하지 않은 응시는 0%가 아니라 —
              { take: 1, mode: '전체 대본 + 하이라이트', time: '05:12', gaze: '—' },
            ].map((t) => (
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
                  <span>응시 {t.gaze}</span>
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
    </Frame>
  );
}
