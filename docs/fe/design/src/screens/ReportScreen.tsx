// 제품 Take 리포트(`frontend/src/features/report/ReportPage.tsx`, `ReportFrame.tsx`)의 설계서 샘플.
import { Button } from '@/ui/Button';
import { Card, Stat } from '@/ui/Panel';
import { Badge } from '@/ui/Badge';
import { Frame, PITCH, type Navigate } from './shared';

export function ReportScreen({ stateId, onNavigate }: { stateId: string; onNavigate: Navigate }) {
  const noGaze = stateId === 'no-gaze';
  const noReview = stateId === 'no-review';
  const ready = stateId === 'default' || noGaze || noReview;
  return (
    <Frame title="발표 리포트" meta="10월 28일 오후 3:12">
      {stateId === 'loading' && (
        <p role="status" className="border-2 border-ink bg-panel p-8 text-center text-stone">
          연습 기록을 불러오는 중이에요.
        </p>
      )}
      {stateId === 'error' && (
        <div role="alert" className="border-2 border-ink bg-panel p-8 text-center">
          <p className="font-bold">연습 기록을 불러오지 못했어요.</p>
          <p className="mt-2 text-sm text-stone">잠시 후 다시 시도해 주세요.</p>
          <div className="mt-5 flex justify-center">
            <Button variant="secondary" onClick={() => onNavigate('home')}>
              홈으로
            </Button>
          </div>
        </div>
      )}
      {ready && (
        <>
          <p className="border-b border-line pb-4 font-bold">
            {PITCH} <span className="mx-2 text-line-strong">/</span> TAKE 2
          </p>
          <section className="flex items-center gap-5 border-2 border-ink bg-panel p-6 shadow-[4px_4px_0_var(--color-line)] sm:p-8">
            <div className="min-w-0">
              <p className="text-xs font-bold text-ink/80">피코의 코칭 노트</p>
              <p role="status" className="mt-3 max-w-3xl text-xl font-bold leading-relaxed">
                {noReview
                  ? '이번 Take는 총평을 만들지 못했어요. 측정값은 그대로 확인할 수 있어요.'
                  : '흐름은 차분했어요. 다음에는 문장 끝에서 청중을 바라보는 시간을 늘려 보세요.'}
              </p>
            </div>
          </section>
          {!noReview && (
            <Card title="지난 Take와 비교">
              <ul className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
                {(
                  [
                    ['멈춤', '14회', '9회', '방향 없음', 'muted'],
                    ['긴 침묵', '3회', '1회', '나아짐', 'good'],
                    ['발표 시간', '05:12', '04:38', '방향 없음', 'muted'],
                    ['분당 군더더기', '0회/분', '0회/분', '같음', 'muted'],
                    ['군더더기', '10회', '7회', '나아짐', 'good'],
                  ] as const
                ).map(([label, prev, cur, badge, tone]) => (
                  <li
                    key={label}
                    className="flex min-w-0 flex-col gap-1.5 border-l-2 border-line pl-3"
                  >
                    <b className="text-sm">{label}</b>
                    <span className="tabular flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm">
                      <span className="text-ink/80">{prev}</span>→<b className="text-lg">{cur}</b>
                      <Badge tone={tone}>{badge}</Badge>
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <section aria-label="측정값" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="발표 시간" value="04:38">
              목표 05:00 · 00:22 남김
            </Stat>
            <Stat label="청중 응시" value={noGaze ? '—' : '68%'}>
              대본 응시 {noGaze ? '—' : '21%'}
            </Stat>
            <Stat label="말하기 속도" value="318자/분">
              Take 평균
            </Stat>
            <Stat label="군더더기" value="4회">
              이번 Take 합계
            </Stat>
          </section>
          <Card title="시선 비율">
            {noGaze ? (
              <p className="text-sm text-ink/80">
                이번 Take는 시선을 재지 않았어요. 말하기 · 시간 기록은 그대로예요.
              </p>
            ) : (
              <p className="text-sm">청중 68% · 화면 8% · 대본 21% · 다른 곳 3%</p>
            )}
          </Card>
          {!noReview && (
            <section className="border-2 border-ink bg-panel p-6">
              <h2 className="text-lg font-bold">Take 3에서 해 볼 것</h2>
              <p className="mt-1 text-sm text-ink/80">
                다음 연습에서 코치가 이 미션을 기준으로 알려 드려요.
              </p>
              <div className="mt-4">
                <Button onClick={() => onNavigate('prepare-mission')}>Take 3 연습하기</Button>
              </div>
            </section>
          )}
          <footer className="flex justify-end border-t border-line-strong pt-5">
            <Button onClick={() => onNavigate('home')}>내 피치로</Button>
          </footer>
        </>
      )}
    </Frame>
  );
}
