// 제품 Take 리포트(`frontend/src/features/report/report/ReportPage.tsx`, `ReportFrame.tsx`)의 설계서 샘플.
import { useEffect, useId, useRef, useState } from 'react';
import { Button } from '@/ui/Button';
import { TakeSurveyDialog } from '@/ui/dialogs/TakeSurveyDialog';
import { Card, Stat } from '@/ui/Panel';
import { Badge, type Tone } from '@/ui/Badge';
import { Frame, PITCH, type Navigate } from './shared';

type Cell = readonly [string, string, string, Tone] | null;

function findOverallCell(rows: [string, ...Cell[]][], index: number): Cell {
  const overallRow = rows.find(([label]) => label === '발표 전체');
  if (!overallRow) return null;
  const [, ...cells] = overallRow;
  return cells[index] ?? null;
}

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

/** 발표 전체만 산출되는 지표를 슬라이드 데이터 누락과 구분합니다. */
const OVERALL_ONLY_LABELS = new Set(['발표 시간', '핵심 내용 전달', '핵심 수치 정확도']);

function comparisonBadgeClass(tone: Tone) {
  if (tone === 'good') return 'bg-coral-deep text-white';
  if (tone === 'bad') return 'border border-line-strong bg-cream text-ink';
  return 'border border-dashed border-line-strong text-stone';
}

function ComparisonSummary() {
  const [open, setOpen] = useState(false);
  const detailTitleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const openerRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!open) {
      if (dialog.open) dialog.close();
      return;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    if (!dialog.open) dialog.showModal();
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);
  return (
    <Card title="지난 Take와 비교">
      <p className="mb-4 text-sm text-ink/80">
        지표별로 지난 연습과의 차이와 비교 가능한 슬라이드를 정리했어요.
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        {COMPARISON.flatMap((group) =>
          group.columns.map((metric, index) => {
            const slides = group.rows.filter((row) => row[0] !== '발표 전체' && row[index + 1]);
            const improved = slides
              .filter((row) => row[index + 1]?.[3] === 'good')
              .map((row) => row[0]);
            const worsened = slides
              .filter((row) => row[index + 1]?.[3] === 'bad')
              .map((row) => row[0]);
            const same = slides.filter((row) => row[index + 1]?.[2] === '같음').length;
            const unknown = slides.filter((row) => row[index + 1]?.[2] === '방향 판단 불가').length;
            const overall = findOverallCell(group.rows, index);
            const directionUnknown = slides.length > 0 && unknown === slides.length && same === 0;
            return (
              <section key={group.title + metric} className="min-w-0 border-l-2 border-line pl-3">
                <h3 className="font-bold">{metric}</h3>
                {directionUnknown && (
                  <p className="mt-2 text-sm text-ink/80">
                    개선·악화 방향을 판단할 수 없는 지표예요. 해당 슬라이드 {unknown}개
                  </p>
                )}
                {slides.length > 0 && !directionUnknown && (
                  <div className="mt-2 grid grid-cols-2 gap-2 text-sm">
                    <div className="border border-coral-deep bg-coral-wash p-3">
                      <p className="font-bold text-ink">개선된 슬라이드</p>
                      <p className="mt-1">{improved.join(', ') || '없음'}</p>
                    </div>
                    <div className="border border-line-strong bg-cream p-3">
                      <p className="font-bold text-ink">다시 점검할 슬라이드</p>
                      <p className="mt-1">{worsened.join(', ') || '없음'}</p>
                    </div>
                  </div>
                )}
                {slides.length === 0 && (
                  <p className="mt-2 text-sm text-ink/80">
                    {overall && OVERALL_ONLY_LABELS.has(metric)
                      ? '발표 전체 기준 지표예요.'
                      : '슬라이드별 비교 결과가 없어요.'}
                  </p>
                )}
                {!directionUnknown && (same > 0 || unknown > 0) && (
                  <p className="mt-2 text-xs text-ink/80">
                    {same > 0 && `변화 없음 ${same}개`}
                    {same > 0 && unknown > 0 && ' · '}
                    {unknown > 0 && `방향 판단 불가 ${unknown}개`}
                  </p>
                )}
                {overall && (
                  <div
                    className={`mt-2 border p-3 text-sm ${overall[3] === 'good' ? 'border-coral-deep bg-coral-wash' : 'border-line-strong bg-cream'}`}
                  >
                    <p className="text-xs font-bold text-stone">발표 전체</p>
                    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
                      <span className="tabular tabular-nums font-bold text-ink">{overall[0]}</span>
                      <span aria-hidden="true" className="text-stone">
                        →
                      </span>
                      <span className="tabular tabular-nums font-bold text-ink">{overall[1]}</span>
                      {overall[3] === 'muted' ? (
                        <Badge tone="muted">{overall[2]}</Badge>
                      ) : (
                        <span
                          className={`inline-flex shrink-0 px-2 py-0.5 text-xs font-bold ${comparisonBadgeClass(overall[3])}`}
                        >
                          {overall[2]}
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </section>
            );
          }),
        )}
      </div>
      <button
        ref={openerRef}
        type="button"
        onClick={() => setOpen(true)}
        className="mt-5 min-h-11 border-2 border-ink bg-panel px-4 py-2 text-sm font-bold hover:bg-cream focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-coral-deep"
      >
        상세 비교표 보기
      </button>
      <dialog
        ref={dialogRef}
        aria-labelledby={detailTitleId}
        onClose={() => {
          setOpen(false);
          openerRef.current?.focus();
        }}
        onCancel={() => setOpen(false)}
        className="fixed inset-0 m-auto max-h-[90dvh] w-[min(96vw,72rem)] max-w-none overflow-y-auto border-2 border-ink bg-panel p-4 text-ink shadow-[6px_6px_0_var(--color-line)] backdrop:bg-ink/60 sm:p-6"
      >
        <div className="mb-5 flex items-start justify-between gap-4">
          <div>
            <h2 id={detailTitleId} className="text-lg font-bold">
              지난 Take 상세 비교
            </h2>
            <p className="mt-1 text-xs text-ink/80">칸마다 지난 Take → 이번 Take</p>
          </div>
          <button
            type="button"
            onClick={() => dialogRef.current?.close()}
            aria-label="상세 비교 닫기"
            className="min-h-11 border-2 border-ink px-4 font-bold focus-visible:outline-2 focus-visible:outline-coral-deep"
          >
            닫기
          </button>
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
                      <th scope="row" className="whitespace-nowrap py-2 pr-4 text-xs font-normal">
                        {label}
                      </th>
                      {cells.map((cell, i) => (
                        <td key={i} className="py-2 pr-4">
                          {cell ? (
                            <span className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
                              <span className="text-ink/80">{cell[0]}</span>→
                              <b className="text-base">{cell[1]}</b>
                              {cell[3] === 'muted' ? (
                                <Badge tone="muted">{cell[2]}</Badge>
                              ) : (
                                <span
                                  className={`inline-flex shrink-0 px-2 py-0.5 text-xs font-bold ${comparisonBadgeClass(cell[3])}`}
                                >
                                  {cell[2]}
                                </span>
                              )}
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
  const ready =
    stateId === 'default' || noGaze || noReview || surveySent || CONTENT_STATES.includes(stateId);
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
          <ContentSample stateId={stateId} />
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

/* ── 내용 전달 (#1154) ─────────────────────────────────────────── */

/** 내용 전달 카드만 바뀌는 상태들 — 나머지 리포트는 기본과 같습니다 */
const CONTENT_STATES = ['content-open', 'content-estimated', 'content-none', 'content-analyzing'];

/**
 * 제품 내용 전달 카드(`ContentCard.tsx` · `SlideCoverage.tsx` · `contentFormat.ts`)의 간추린 샘플.
 * 숫자는 #1152 예시 그대로입니다. 막대는 그리지 않고 글자 범례만 둡니다 (productNote)
 */
function ContentSample({ stateId }: { stateId: string }) {
  if (stateId === 'content-none')
    return (
      <Card title="내용 전달">
        <p className="text-sm text-ink/80">대본 전달도 상세 결과를 아직 제공하지 않아요</p>
      </Card>
    );
  if (stateId === 'content-analyzing')
    return (
      <Card title="내용 전달">
        <p className="text-sm text-ink/80">대본 전달도를 분석하고 있어요</p>
      </Card>
    );
  if (stateId === 'content-estimated')
    return (
      <Card title="내용 전달">
        <p className="text-sm font-bold text-stone">핵심 내용 전달률</p>
        <p className="flex flex-wrap items-center gap-2 font-bold">
          대부분 말함 1장 <Badge tone="muted">간이</Badge>
        </p>
        <p className="flex flex-wrap items-center gap-2 text-xs text-ink/80">
          <Badge tone="muted">일부 장 제외</Badge>1장 중 0장 평가 · 1장 간이
        </p>
        {/* 제품처럼 채점된 장이 없으면 Take 전체의 0 개수 대신 집계하지 못했다고 알립니다 */}
        <p className="text-sm text-ink/80">
          채점된 장이 없어 핵심 수치 · 표현 · 말의 구성은 집계하지 못했어요. 장별 간이 결과를 확인해
          주세요.
        </p>
        <SlideRows open={false} estimatedOnly />
      </Card>
    );
  return (
    <Card title="내용 전달">
      <dl className="grid gap-4 sm:grid-cols-2">
        <div>
          <dt className="text-sm font-bold text-stone">핵심 내용 전달률</dt>
          <dd className="mt-2 text-3xl font-bold">50%</dd>
        </div>
        <div>
          <dt className="text-sm font-bold text-stone">다르게 말함</dt>
          <dd className="mt-2 text-3xl font-bold">33%</dd>
        </div>
      </dl>
      <p className="flex flex-wrap items-center gap-2 text-xs text-ink/80">
        <Badge tone="muted">일부 장 제외</Badge>2장 중 1장 평가 · 1장 간이
      </p>
      <p className="text-sm">
        지난 Take보다 <b>+8%p</b>
        <span className="ml-2 text-xs text-ink/80">일부 장이 평가에서 제외됐어요</span>
      </p>
      <p className="text-sm">전달 50% · 일부 0% · 빠뜨림 17% · 다르게 말함 33%</p>
      <p className="text-sm">
        <b>놓친 중요 내용</b> 3장 · 신규 고객 비중 60% · <b>다르게 말함</b>
      </p>
      {/* 제품처럼 넓은 화면에서는 수치 · 표현 · 말의 구성을 한 줄에 */}
      <div className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
        <p>
          <b className="block">핵심 수치</b> 확인한 2개 중 2개 맞음 · 1개 확인 대기
        </p>
        <p>
          <b className="block">표현</b> 대본대로 1 · 바꿔 말함 1 · 구분 어려움 0
        </p>
        <p>
          <b className="block">말의 구성</b> 대본 내용 75% · 진행 말 10% · 대본 밖 말 15%
          <span className="block text-xs text-ink/80">대본 밖 말은 세부 구분 없이 합쳐 보여요</span>
        </p>
      </div>
      <SlideRows open={stateId === 'content-open'} />
    </Card>
  );
}

/**
 * 장별 목록. "장별 보기 (N장)"로 접어 두고, 열면 장마다 한 줄 — 장이 많아도 카드가 길어지지 않게 합니다.
 * 줄을 누르면 그 장을 펼칩니다. 제품은 펼칠 때 장별 상세를 부릅니다.
 * `estimatedOnly` 는 채점된 장 없이 간이 장 하나만 있는 결과(`content-estimated`)라 간이 줄 하나만 그립니다
 */
function SlideRows({
  open: initial,
  estimatedOnly = false,
}: {
  open: boolean;
  estimatedOnly?: boolean;
}) {
  const [listOpen, setListOpen] = useState(initial);
  const [open, setOpen] = useState(initial);
  return (
    <section className="flex flex-col gap-2">
      <button
        type="button"
        aria-expanded={listOpen}
        onClick={() => setListOpen((v) => !v)}
        className="flex min-h-11 w-full items-center justify-between gap-3 border-2 border-ink bg-cream px-3 text-left text-sm font-bold"
      >
        <span>
          장별 보기 <span className="font-normal text-stone">({estimatedOnly ? 1 : 2}장)</span>
        </span>
        <span aria-hidden="true">{listOpen ? '▴' : '▾'}</span>
      </button>
      {listOpen && estimatedOnly && (
        <ul className="border-t border-line text-sm">
          <li className="flex min-h-11 items-center gap-3 border-b border-line py-2">
            <b className="w-10 shrink-0">1장</b>
            대부분 말함 <Badge tone="muted">간이</Badge>
          </li>
        </ul>
      )}
      {listOpen && !estimatedOnly && (
        <ul className="border-t border-line text-sm">
          <li className="border-b border-line">
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              className="flex min-h-11 w-full items-start gap-3 py-2 text-left"
            >
              <b className="w-10 shrink-0">3장</b>
              <span className="flex-1">
                전달 <b>50%</b> · 다르게 말함 <b>33%</b> · 놓친 중요 내용 <b>1</b>
              </span>
              <span aria-hidden="true">{open ? '▴' : '▾'}</span>
            </button>
            {open && <SlideDetail />}
          </li>
          <li className="flex min-h-11 items-center gap-3 border-b border-line py-2">
            <b className="w-10 shrink-0">4장</b>
            절반쯤 말함 <Badge tone="muted">간이</Badge>
          </li>
        </ul>
      )}
    </section>
  );
}

function SlideDetail() {
  return (
    <div className="flex flex-col gap-4 border-t border-line bg-cream/40 px-1 py-3">
      <section className="flex flex-col gap-2">
        <h4 className="text-sm font-bold">대본 문장</h4>
        <p className="flex flex-wrap items-center gap-2">
          <Badge tone="strong">전달</Badge>
          <Badge tone="muted">바꿔 말함</Badge>
          지난 분기 매출은 37% 늘었습니다.
        </p>
        <p className="flex flex-wrap items-center gap-2">
          <Badge tone="bad">다르게 말함</Badge>
          신규 고객 비중은 60%입니다.
        </p>
        <p className="flex flex-wrap items-center gap-2">
          <Badge tone="muted">채점 안 함</Badge>
          (다음 장으로 넘어갑니다)
        </p>
      </section>
      <section className="flex flex-col gap-2">
        <h4 className="text-sm font-bold">내가 말한 문장</h4>
        <p>
          <mark className="bg-coral-wash px-0.5 text-ink">
            안녕하세요<span className="ml-1 text-xs font-bold text-coral-deep">[진행 말]</span>
          </mark>{' '}
          지난 분기 매출은 삼십칠 퍼센트 늘었는데요
        </p>
      </section>
      <p className="text-sm">
        <b>핵심 수치</b> 대본 60% · 들린 값 40% · <b>다른 값</b>
      </p>
    </div>
  );
}
