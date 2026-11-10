// 제품 Take 리포트(`frontend/src/features/report/ReportPage.tsx`, `ReportFrame.tsx`)의 설계서 샘플.
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/ui/Button';
import { TakeSurveyDialog } from '@/ui/dialogs/TakeSurveyDialog';
import { Card, Stat } from '@/ui/Panel';
import type { Tone } from '@/ui/Badge';
import { Frame, PITCH, type Navigate } from './shared';

type Cell = readonly [string, string, string, Tone] | null;

/** 지난 Take와 비교 샘플 — 제품은 AI `comparison` 을 차원별로 묶습니다. 분당 군더더기는 보이지 않습니다 (#929) */
const COMPARISON: { title: string; columns: string[]; rows: [string, ...Cell[]][] }[] = [
  {
    title: '대본에서 벗어나기',
    columns: ['대본 응시', '대본 읽기 횟수'],
    rows: [
      ['발표 전체', ['27%', '22%', '개선 ↑', 'good'], ['4회', '2회', '개선 ↑', 'good']],
      ['3번 장', ['70%', '62%', '개선 ↑', 'good'], null],
    ],
  },
  {
    title: '속도 · 리듬',
    columns: ['멈춤', '긴 침묵', '발표 시간', '장 시간'],
    rows: [
      [
        '발표 전체',
        ['14회', '9회', '방향 판단 불가', 'muted'],
        ['3회', '1회', '개선 ↑', 'good'],
        ['05:12', '04:38', '개선 ↑', 'good'],
        null,
      ],
      ['1번 장', null, null, null, ['01:10', '00:58', '개선 ↑', 'good']],
      ['2번 장', null, null, null, ['00:52', '00:55', '악화 ↓', 'bad']],
    ],
  },
  {
    title: '군더더기 없이',
    columns: ['군더더기'],
    rows: [['발표 전체', ['10회', '7회', '개선 ↑', 'good']]],
  },
];

function comparisonBadgeClass(tone: Tone) {
  if (tone === 'good') return 'bg-coral-deep text-white';
  if (tone === 'bad') return 'border border-line-strong bg-cream text-ink';
  return 'border border-dashed border-line-strong text-stone';
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
          {!noReview && (
            <Card
              title="지난 Take와 비교"
              action={<p className="text-xs text-ink/80">칸마다 지난 Take → 이번 Take</p>}
            >
              {/* 제품의 지표 요약 구조: 슬라이드는 구분된 카드, 전체 지표는 값과 판정을 한 박스로 표시 */}
              <div className="grid gap-4 lg:grid-cols-2">
                <section className="min-w-0 border-l-2 border-line pl-3">
                  <h3 className="font-bold">대본 응시</h3>
                  <div className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
                    <div className="border border-coral-deep bg-coral-wash p-3">
                      <p className="font-bold text-ink">개선된 슬라이드</p>
                      <p className="mt-1">3번</p>
                    </div>
                    <div className="border border-line-strong bg-cream p-3">
                      <p className="font-bold text-ink">다시 점검할 슬라이드</p>
                      <p className="mt-1">없음</p>
                    </div>
                  </div>
                </section>
                <section className="min-w-0 border-l-2 border-line pl-3">
                  <h3 className="font-bold">발표 시간</h3>
                  <div className="mt-2 border border-line-strong bg-panel p-3">
                    <p className="text-xs font-bold text-stone">발표 전체</p>
                    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
                      <div>
                        <p className="text-xs text-stone">지난 Take</p>
                        <p className="tabular tabular-nums font-bold text-ink">05:12</p>
                      </div>
                      <span aria-hidden="true" className="text-stone">
                        →
                      </span>
                      <div>
                        <p className="text-xs text-stone">이번 Take</p>
                        <p className="tabular tabular-nums font-bold text-ink">04:38</p>
                      </div>
                      <span className="inline-flex shrink-0 bg-coral-deep px-2 py-0.5 text-xs font-bold text-white">
                        개선 ↑
                      </span>
                    </div>
                  </div>
                </section>
              </div>
              <p className="text-xs text-ink/80">
                아래는 상세 비교표의 미리보기예요. 제품에서는 ‘상세 비교표 보기’를 눌러 확인해요.
              </p>
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
                                    <span
                                      className={`inline-flex shrink-0 px-2 py-0.5 text-xs font-bold ${comparisonBadgeClass(cell[3])}`}
                                    >
                                      {cell[2]}
                                    </span>
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
          <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-line-strong pt-5">
            <SurveyLine sent={surveySent} />
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
function SurveyLine({ sent }: { sent: boolean }) {
  const [open, setOpen] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const status = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (submitted) status.current?.focus();
  }, [submitted]);
  if (sent || submitted)
    return (
      <p ref={status} tabIndex={-1} role="status" className="text-sm text-ink/80">
        설문을 보냈어요. 다음 분석을 고치는 데 쓸게요.
      </p>
    );
  return (
    <>
      <Button variant="secondary" onClick={() => setOpen(true)}>
        이번 분석 설문 남기기
      </Button>
      {open && (
        <TakeSurveyDialog onClose={() => setOpen(false)} onSent={() => setSubmitted(true)} />
      )}
    </>
  );
}
