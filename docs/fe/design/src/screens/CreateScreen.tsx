// 제품 피치 생성 · 수정(`frontend/src/features/pitch/create/PitchCreatePage.tsx`, `steps/*`)의 설계서 샘플.
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { Button } from '@/ui/Button';
import { PitchCoachWordmark } from '@/ui/PitchCoachWordmark';
import { PaneHeading } from '@/ui/PaneHeading';
import { PaneFooter, primaryButton, secondaryButton } from '@/ui/PaneFooter';
import { PITCH, type Navigate } from './shared';

// 제품 `pitch/create/lib/draft.ts`의 STEPS와 단계별 머리(steps/*Pane.tsx)
const STEPS = [
  { label: '슬라이드', title: '슬라이드', subtitle: '올린 자료를 확인해 주세요.' },
  {
    label: '대본',
    title: '대본 입력',
    subtitle: '선택한 슬라이드에 맞춰 대본을 준비해 주세요.',
  },
  {
    label: '발표정보 확인',
    title: '발표정보 확인',
    subtitle: '채워 둔 값을 확인하고 발표 날짜를 골라 주세요.',
  },
  {
    label: '평가기준',
    title: '평가기준 (선택)',
    subtitle: '연습할 때 무엇을 볼지 정해요. 건너뛰어도 연습할 수 있어요.',
    optional: true,
  },
];

export function CreateScreen({
  stateId,
  onNavigate,
  inline,
}: {
  stateId: string;
  onNavigate: Navigate;
  inline: boolean;
}) {
  const [step, setStep] = useState(0);
  const hasSlides = stateId !== 'empty' && stateId !== 'error';
  const dialogRef = useRef<HTMLDialogElement>(null);
  const previewing = stateId === 'preview';
  useEffect(() => {
    // 개별 보기에서만 페이지를 막는 창으로 연다. 전체 보기는 아래에서 open 속성으로 그 자리에 둔다
    if (previewing && !inline) dialogRef.current?.showModal();
  }, [previewing, inline]);
  const current = STEPS[step]!;

  return (
    <div className="pitch-create pitch-create-square flex min-h-[40rem] flex-col bg-greige text-ink">
      <header className="flex shrink-0 items-center justify-between border-b border-line-strong bg-panel px-6 py-4">
        <Link to="/" aria-label="PITCH COACH 홈">
          <PitchCoachWordmark />
        </Link>
        <Link to="/" className="text-sm font-bold hover:text-ink">
          ← 내 피치
        </Link>
      </header>
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {/* 제품은 여기에 자료 버전 트리(VersionRail)와 치치를 둔다. 설계서는 버전 목록만 줄여 그린다 */}
        <aside className="flex shrink-0 flex-col gap-2 border-b border-line-strong bg-cream/60 p-4 text-sm lg:w-sidebar lg:border-b-0 lg:border-r">
          <p className="font-bold">발표 자료</p>
          <p>{hasSlides ? '슬라이드 V1 · 12장' : '슬라이드 —'}</p>
          <p className="text-ink/80">대본 —</p>
          <p className="text-ink/80">평가기준 —</p>
        </aside>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 p-4">
          <nav aria-label="경로" className="flex flex-wrap items-center gap-2 text-xs text-ink/80">
            <Link to="/" className="hover:text-ink">
              내 피치
            </Link>
            <span aria-hidden="true">›</span>
            <span aria-current="page" className="text-ink">
              {hasSlides ? PITCH : '새 피치'}
            </span>
          </nav>
          <ol aria-label="진행 단계" className="grid shrink-0 grid-cols-2 gap-2 lg:grid-cols-4">
            {STEPS.map((s, index) => {
              const on = index === step;
              return (
                <li key={s.label}>
                  <button
                    type="button"
                    onClick={() => setStep(index)}
                    disabled={!hasSlides && index > 0}
                    aria-current={on ? 'step' : undefined}
                    className={[
                      'flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left text-sm disabled:cursor-not-allowed disabled:bg-cream disabled:text-ink/80',
                      on
                        ? 'border-ink bg-panel font-bold'
                        : 'border-line bg-panel/60 hover:border-line-strong',
                    ].join(' ')}
                  >
                    <span
                      className={`tabular flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${on ? 'bg-ink text-white' : 'border border-line-strong text-ink/80'}`}
                    >
                      {index + 1}
                    </span>
                    <span>
                      {s.label}
                      {s.optional && (
                        <span className="ml-1 text-xs font-normal text-ink/80">(선택)</span>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
          <section className="flex min-h-[26rem] flex-1 flex-col rounded-lg border border-line bg-panel p-2 sm:p-5">
            <PaneHeading
              title={current.title}
              subtitle={
                step === 0 && !hasSlides ? '발표에 사용할 PDF부터 올려 주세요.' : current.subtitle
              }
            />
            {step === 0 && (
              <SlideStep stateId={stateId} hasSlides={hasSlides} onNavigate={onNavigate} />
            )}
            {step === 1 && (
              <label className="flex flex-1 flex-col gap-3 font-bold">
                대본
                <textarea
                  className="min-h-40 w-full rounded-lg border border-line-strong bg-panel p-4 font-normal"
                  defaultValue={'[1]\n안녕하세요. 오늘 준비한 발표를 시작하겠습니다.'}
                />
              </label>
            )}
            {step === 2 && (
              <div className="flex flex-1 flex-col gap-4">
                <label className="flex flex-col gap-2 font-bold">
                  발표 제목
                  <input
                    className="rounded-lg border border-line-strong bg-panel p-3 font-normal"
                    defaultValue={PITCH}
                  />
                </label>
                <label className="flex flex-col gap-2 font-bold">
                  목표 발표시간
                  <input
                    className="rounded-lg border border-line-strong bg-panel p-3 font-normal"
                    defaultValue="5분"
                  />
                </label>
              </div>
            )}
            {step === 3 && (
              <div className="flex-1">
                <p>발표에서 특히 신경 쓸 기준을 적어요.</p>
                <label className="mt-4 flex items-center gap-2">
                  <input type="checkbox" /> 청중을 70% 이상 바라보기
                </label>
              </div>
            )}
            <CreateFooter
              step={step}
              hasSlides={hasSlides}
              onStep={setStep}
              onStart={() => onNavigate('prepare-mission')}
            />
          </section>
        </div>
      </div>
      {previewing && (
        <dialog
          ref={dialogRef}
          open={inline || undefined}
          onClose={() => onNavigate('pitch-create', 'default')}
          aria-label="슬라이드 미리보기"
          className={`w-full max-w-3xl border-2 border-ink bg-panel p-5 text-ink ${inline ? 'static mx-auto my-4' : 'm-auto backdrop:bg-ink/40'}`}
        >
          <header className="flex justify-between">
            <h2 className="font-bold">슬라이드 1 / 12</h2>
            <Button
              autoFocus={!inline}
              variant="secondary"
              onClick={() => onNavigate('pitch-create', 'default')}
            >
              닫기
            </Button>
          </header>
          <div className="mt-4 flex min-h-64 items-center justify-center bg-cream p-6">
            <p className="text-2xl font-bold">{PITCH}</p>
          </div>
        </dialog>
      )}
    </div>
  );
}

function SlideStep({
  stateId,
  hasSlides,
  onNavigate,
}: {
  stateId: string;
  hasSlides: boolean;
  onNavigate: Navigate;
}) {
  return (
    <>
      {hasSlides ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 bg-cream p-6">
          <p className="font-bold">{PITCH}.pdf · 12장</p>
          <Button variant="secondary" onClick={() => onNavigate('pitch-create', 'preview')}>
            슬라이드 크게 보기
          </Button>
        </div>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center rounded-lg border-2 border-dashed border-line-strong bg-panel p-8 text-center">
          <p className="mt-5 text-lg font-bold">PDF를 여기에 끌어다 놓으세요</p>
          <p className="mt-2 text-sm text-ink/80">
            파일 이름으로 발표 제목을 채워 드려요 · 나중에 고칠 수 있어요 · 최대 40MB
          </p>
          <button
            type="button"
            className={`${primaryButton} mt-4`}
            onClick={() => onNavigate('pitch-create', 'default')}
          >
            PDF 파일 선택
          </button>
        </div>
      )}
      {stateId === 'error' && (
        <div role="alert" className="my-3">
          <p className="font-bold text-coral-deep">
            업로드하지 못했어요. 네트워크를 확인하고 다시 시도해 주세요.
          </p>
          <button
            type="button"
            className={`${secondaryButton} mt-3`}
            onClick={() => onNavigate('pitch-create', 'default')}
          >
            다시 시도
          </button>
        </div>
      )}
    </>
  );
}

/** 단계별 아래 줄. 문구 · 버튼은 제품 각 단계(SlidePane · ScriptPane · InfoPane · CriteriaPane)와 같다 */
function CreateFooter({
  step,
  hasSlides,
  onStep,
  onStart,
}: {
  step: number;
  hasSlides: boolean;
  onStep: (step: number) => void;
  onStart: () => void;
}) {
  const back = (
    <button type="button" onClick={() => onStep(step - 1)} className={secondaryButton}>
      ← 이전
    </button>
  );
  if (step === 0)
    return (
      <PaneFooter hint="다음은 슬라이드에 맞춰 대본을 붙여요.">
        <button
          type="button"
          disabled={!hasSlides}
          onClick={() => onStep(1)}
          className={primaryButton}
        >
          다음 · 대본 →
        </button>
      </PaneFooter>
    );
  if (step === 1)
    return (
      <PaneFooter hint="슬라이드마다 “[N]” 같은 구분자를 새 줄에 적어 주세요. N은 슬라이드 번호예요.">
        {back}
        <button type="button" onClick={() => onStep(2)} className={primaryButton}>
          대본 매핑
        </button>
      </PaneFooter>
    );
  if (step === 2)
    return (
      <PaneFooter hint="평가기준은 골라도 되고 건너뛰어도 돼요.">
        {back}
        <button type="button" onClick={() => onStep(3)} className={primaryButton}>
          다음 →
        </button>
      </PaneFooter>
    );
  // 저장한 평가기준이 없으면 "연습 시작"은 막히고 "건너뛰기"로 시작한다(제품 CriteriaPane StartFooter)
  return (
    <PaneFooter hint="연습할 준비가 됐어요.">
      {back}
      <button type="button" onClick={onStart} className={secondaryButton}>
        건너뛰기
      </button>
      <button type="button" disabled className={primaryButton}>
        연습 시작 →
      </button>
    </PaneFooter>
  );
}
