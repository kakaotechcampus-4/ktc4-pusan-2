// 제품 Take 리포트(`frontend/src/features/report/ReportPage.tsx`, `ReportFrame.tsx`)의 설계서 샘플.
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/ui/Button';
import { TakeSurveyDialog } from '@/ui/dialogs/TakeSurveyDialog';
import { Card, Stat } from '@/ui/Panel';
import { Badge, type Tone } from '@/ui/Badge';
import { Frame, PITCH, type Navigate } from './shared';

type Cell = readonly [string, string, string, Tone] | null;

/** 지난 Take와 비교 샘플 — 제품은 AI `comparison` 을 차원별로 묶습니다. 분당 군더더기는 보이지 않습니다 (#929) */
const COMPARISON: { title: string; columns: string[]; rows: [string, ...Cell[]][] }[] = [
  {
    title: '대본에서 벗어나기',
    columns: ['대본 응시', '대본 읽기 횟수'],
    rows: [
      ['발표 전체', ['27%', '22%', '나아짐', 'good'], ['4회', '2회', '나아짐', 'good']],
      ['3번 장', ['70%', '62%', '나아짐', 'good'], null],
    ],
  },
  {
    title: '속도 · 리듬',
    columns: ['멈춤', '긴 침묵', '발표 시간', '장 시간'],
    rows: [
      [
        '발표 전체',
        ['14회', '9회', '방향 없음', 'muted'],
        ['3회', '1회', '나아짐', 'good'],
        ['05:12', '04:38', '나아짐', 'good'],
        null,
      ],
      ['1번 장', null, null, null, ['01:10', '00:58', '나아짐', 'good']],
      ['2번 장', null, null, null, ['00:52', '00:55', '나빠짐', 'bad']],
    ],
  },
  {
    title: '군더더기 없이',
    columns: ['군더더기'],
    rows: [['발표 전체', ['10회', '7회', '나아짐', 'good']]],
  },
];

function ComparisonSummary() {
  const [open, setOpen] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const openerRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <Card title="지난 Take와 비교">
      <p className="mb-4 text-sm text-ink/80">지표별로 지난 연습보다 달라진 슬라이드를 모았어요.</p>
      <div className="grid gap-4 lg:grid-cols-2">
        {COMPARISON.flatMap((group) => group.columns.map((metric, index) => {
          const slides = group.rows.filter((row) => row[0] !== '발표 전체' && row[index + 1]);
          const improved = slides.filter((row) => row[index + 1]?.[3] === 'good').map((row) => row[0]);
          const worsened = slides.filter((row) => row[index + 1]?.[3] === 'bad').map((row) => row[0]);
          const same = slides.filter((row) => row[index + 1]?.[2] === '같음').length;
          const unknown = slides.filter((row) => row[index + 1]?.[2] === '방향 없음').length;
          const overall = group.rows.find((row) => row[0] === '발표 전체')?.[index + 1];
          return (
            <section key={group.title + metric} className="min-w-0 border-l-2 border-line pl-3">
              <h3 className="font-bold">{metric}</h3>
              {slides.length ? <div className="mt-2 grid grid-cols-2 gap-2 text-sm">
                <div className="bg-cream p-3"><p className="font-bold">나아진 슬라이드</p><p className="mt-1">{improved.join(', ') || '없음'}</p></div>
                <div className="bg-panel p-3"><p className="font-bold">나빠진 슬라이드</p><p className="mt-1">{worsened.join(', ') || '없음'}</p></div>
              </div> : <p className="mt-2 text-sm text-ink/80">슬라이드별 비교 데이터가 없어요.</p>}
              {(same > 0 || unknown > 0) && <p className="mt-2 text-xs text-ink/80">{same > 0 ? `변화 없음 ${same}개` : ''}{unknown > 0 ? ` · 방향 판단 불가 ${unknown}개` : ''}</p>}
              {overall && <p className="mt-2 text-xs text-ink/80">발표 전체: {overall[0]} → {overall[1]} · {overall[2]}</p>}
            </section>
          );
        }))}
      </div>
      <button ref={openerRef} type="button" onClick={() => setOpen(true)} className="mt-5 min-h-11 border-2 border-ink bg-panel px-4 py-2 text-sm font-bold hover:bg-cream">슬라이드별 상세 비교표 보기</button>
      <dialog ref={dialogRef} aria-labelledby="comparison-detail-title"
        onClose={() => { setOpen(false); openerRef.current?.focus(); }}
        onCancel={() => setOpen(false)}
        className="fixed inset-0 m-auto max-h-[90dvh] w-[min(96vw,72rem)] max-w-none overflow-y-auto border-2 border-ink bg-panel p-4 text-ink shadow-[6px_6px_0_var(--color-line)] backdrop:bg-ink/60 sm:p-6">
        <div className="mb-5 flex items-start justify-between gap-4">
          <div><h2 id="comparison-detail-title" className="text-lg font-bold">지난 Take 상세 비교</h2><p className="mt-1 text-xs text-ink/80">칸마다 지난 Take → 이번 Take</p></div>
          <button type="button" onClick={() => setOpen(false)} aria-label="상세 비교 닫기" className="min-h-11 border-2 border-ink px-4 font-bold">닫기</button>
        </div>
              {/* 리뷰 차원(`review-agent.md` 5-3)마다 표 하나 — 지표가 열, 발표 전체 · 장이 행 (#929) */}
              <div className="grid gap-4 lg:grid-cols-2">
                {COMPARISON.map((g) => (
                  <section
                    key={g.title}
                    aria-label={g.title}
                    className={`min-w-0 border-l-2 border-line pl-3 ${g.columns.length > 2 ? 'lg:col-span-2' : ''}`}
                  >
                    <h3 className="text-sm font-bold">{g.title}</h3>
                    <table className="tabular mt-2 w-full text-left text-sm">
                      <thead>
                        <tr className="border-b border-line text-xs text-ink/80">
                          <th scope="col" className="w-20 py-1.5 pr-4 font-normal">
                            구간
                          </th>
                          {g.columns.map((c) => (
                            <th key={c} scope="col" className="py-1.5 pr-4 font-bold text-ink">
                              {c}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {g.rows.map(([label, ...cells]) => (
                          <tr key={label} className="border-b border-line last:border-b-0">
                            <th
                              scope="row"
                              className="whitespace-nowrap py-2 pr-4 text-xs font-normal"
                            >
                              {label}
                            </th>
                            {cells.map((cell, i) => (
                              <td key={i} className="py-2 pr-4">
                                {cell ? (
                                  <span className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
                                    <span className="text-ink/80">{cell[0]}</span>→
                                    <b className="text-base">{cell[1]}</b>
                                    <Badge tone={cell[3]}>{cell[2]}</Badge>
                                  </span>
                                ) : (
                                  <span className="text-ink/80">—</span>
                                )}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </section>
                ))}
              </div>
      </dialog>
    </Card>
  );
}

export function ReportScreen({ stateId, onNavigate }: { stateId: string; onNavigate: Navigate }) {
  const noGaze = stateId === 'no-gaze';
  const noReview = stateId === 'no-review';
  const surveySent = stateId === 'survey-sent';
  const ready = stateId === 'default' || noGaze || noReview || surveySent;
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
          {!noReview && <ComparisonSummary />}
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
          <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-line-strong pt-5">
            <SurveyLine sent={surveySent} onSent={() => onNavigate('report', 'survey-sent')} />
            <Button className="ml-auto" onClick={() => onNavigate('home')}>
              내 피치로
            </Button>
          </footer>
        </>
      )}
    </Frame>
  );
}

/**
 * 리포트 아래 설문 줄 (제품 `TakeSurvey.tsx`, #776). 분석이 끝난 리포트에만 두고, 버튼으로만 연다.
 * 보낸 리포트에는 버튼 대신 보냈다는 줄만 둔다
 */
function SurveyLine({ sent, onSent }: { sent: boolean; onSent: () => void }) {
  const [open, setOpen] = useState(false);
  if (sent)
    return (
      <p role="status" className="text-sm text-ink/80">
        설문을 보냈어요. 다음 분석을 고치는 데 쓸게요.
      </p>
    );
  return (
    <>
      <Button variant="secondary" onClick={() => setOpen(true)}>
        이번 분석 설문 남기기
      </Button>
      {open && <TakeSurveyDialog onClose={() => setOpen(false)} onSent={onSent} />}
    </>
  );
}
