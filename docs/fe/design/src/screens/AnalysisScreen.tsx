// 제품 분석 대기 · 대본 확인(`frontend/src/features/report/ProcessingPage.tsx`, `ConfirmPage.tsx`)의 설계서 샘플. 둘 다 노을 화면을 쓴다.
import { useState, type ReactNode } from 'react';
import { Button } from '@/ui/Button';
import { DuskScreen, StatusLine, type DuskReport } from '@/ui/DuskScreen';
import { PITCH, type Navigate } from './shared';

const SAMPLE_REPORT: DuskReport = {
  pitch_title: PITCH,
  take_number: 2,
  measures: { duration_ms: 278_000 },
};

const STEP_NAMES = ['발표 기록 정리', '말한 내용 채점', '코치 총평'];

/** 단계 칸을 읽어 줄 말. 색만으로 구분하지 않는다(제품 ProcessingPage STEP_LABEL) */
function stepLabel(index: number, current: number, waiting: boolean) {
  if (index < current) return '끝남';
  if (index > current) return '대기';
  return waiting ? '확인 대기' : '진행 중';
}

export function ProcessingScreen({
  stateId,
  onNavigate,
}: {
  stateId: string;
  onNavigate: Navigate;
}) {
  const waiting = stateId === 'confirm';
  // 분석 중은 두 번째 단계, 확인 대기는 두 번째 단계에서 멈춘 것으로 그린다
  const current = 1;
  let body: ReactNode;
  if (stateId === 'error')
    body = (
      <div role="alert" className="flex flex-col items-center gap-4">
        <p className="text-lg font-bold">연습 기록을 불러오지 못했어요.</p>
        <p className="text-sm">잠시 후 다시 시도해 주세요.</p>
        <Button variant="secondary" onClick={() => onNavigate('home')}>
          홈으로
        </Button>
      </div>
    );
  else if (stateId === 'loading') body = <StatusLine text="연습 기록을 불러오는 중" />;
  else if (stateId === 'failed')
    body = (
      <div role="alert" className="flex flex-col items-center gap-4">
        <p className="text-lg font-bold">이번 Take는 분석하지 못했어요</p>
        <p className="text-sm leading-relaxed">
          발표 기록이 너무 짧거나 측정이 거의 되지 않았을 수 있어요. 이번 Take는 연습 기록에
          &apos;분석 실패&apos;로 남아요.
        </p>
        <div className="flex flex-wrap justify-center gap-3">
          <Button onClick={() => onNavigate('prepare-mission')}>다시 연습하기</Button>
          <Button variant="secondary" onClick={() => onNavigate('home')}>
            홈으로
          </Button>
        </div>
      </div>
    );
  else if (stateId === 'held')
    body = (
      <div role="alert" className="flex flex-col items-center gap-4">
        <p className="text-lg font-bold">분석이 잠시 멈췄어요</p>
        <p className="text-sm leading-relaxed">
          말한 내용을 채점하다 문제가 생겨 확인하고 있어요. 바로 끝나지 않으니 홈에서 나중에 다시
          확인해 주세요.
        </p>
        <Button variant="secondary" onClick={() => onNavigate('home')}>
          홈으로
        </Button>
      </div>
    );
  else
    body = (
      <>
        <StatusLine text={waiting ? '확인을 기다리는 중' : '분석 중'} />
        <ol className="flex w-full max-w-md gap-1.5" aria-label="분석 단계">
          {STEP_NAMES.map((name, i) => (
            <li key={name} className="flex-1">
              <span
                aria-hidden="true"
                className={`block h-3 border-2 border-dusk-line ${i <= current ? 'bg-dusk-accent' : ''} ${i < current ? 'opacity-55' : ''}`}
              />
              <span className="sr-only">
                {name} {stepLabel(i, current, waiting)}
              </span>
            </li>
          ))}
        </ol>
        <p aria-live="polite" className="font-mono text-xs text-dusk-accent">
          {current + 1}/3 · {STEP_NAMES[current]}
        </p>
        {waiting && (
          <>
            <p className="text-sm leading-relaxed">
              비슷하게 들린 말이 있어요. 어떻게 말했는지 알려 주면 채점에 반영되고, 답이 없으면 10분
              안에 그대로 진행돼요.
            </p>
            <Button onClick={() => onNavigate('confirm')}>비슷하게 들린 말 확인하기</Button>
          </>
        )}
        <div className="w-full border-t-2 border-dashed border-dusk-line pt-4 text-left">
          <p className="font-mono text-xs font-bold text-dusk-accent">TIP · 치치</p>
          <p className="mt-1 text-sm leading-relaxed">
            문장 끝에서 고개를 들어 봐. 청중과 눈 맞추기가 훨씬 쉬워져.
          </p>
        </div>
        <p className="text-xs text-dusk-accent">
          {!waiting && '보통 1분 안에 끝나요. '}화면을 떠나도 분석은 계속되고, 홈에서 다시 볼 수
          있어요.
        </p>
        <Button variant="secondary" size="sm" onClick={() => onNavigate('home')}>
          홈으로
        </Button>
      </>
    );
  return (
    <DuskScreen report={SAMPLE_REPORT}>
      <h1 className="sr-only">분석 대기</h1>
      {body}
    </DuskScreen>
  );
}

const CONFIRM_OPTIONS = [
  { value: 'as_script', label: '대본대로 말했어요', word: '37%' },
  { value: 'as_stt', label: '들린 대로 말했어요', word: '삼십칠 퍼센트' },
  { value: 'unsure', label: '잘 모르겠어요', word: null },
];

export function ConfirmScreen({ stateId, onNavigate }: { stateId: string; onNavigate: Navigate }) {
  const [pick, setPick] = useState<string | null>(null);
  const back = () => onNavigate('processing-loading');
  let body: ReactNode;
  if (stateId === 'error')
    body = (
      <div role="alert" className="flex flex-col items-center gap-4">
        <p className="text-lg font-bold">확인할 말을 불러오지 못했어요.</p>
        <p className="text-sm">잠시 후 다시 시도해 주세요.</p>
        <div className="flex flex-wrap justify-center gap-3">
          <Button onClick={() => onNavigate('confirm', 'default')}>다시 불러오기</Button>
          <Button variant="secondary" onClick={back}>
            분석 대기로
          </Button>
        </div>
      </div>
    );
  else if (stateId === 'loading') body = <StatusLine text="확인할 말을 불러오는 중" />;
  else if (stateId === 'timeout' || stateId === 'closed')
    body = (
      <div role="alert" className="flex flex-col items-center gap-4">
        <p className="text-lg font-bold">
          {stateId === 'timeout' ? '확인 시간이 끝났어요' : '확인할 말이 없어요'}
        </p>
        <p className="text-sm leading-relaxed">
          {stateId === 'timeout'
            ? '답 없이 그대로 진행돼요.'
            : '확인 시간이 끝났거나 이미 답했어요. 분석은 그대로 이어져요.'}
        </p>
        <Button onClick={back}>분석 대기로</Button>
      </div>
    );
  else
    body = (
      <>
        <h1 className="text-lg font-bold sm:text-xl">비슷하게 들린 말이 있어요</h1>
        <p className="text-sm leading-relaxed">
          확인할 항목이 1개 있어요. 현재 내용 점수는 이 부분을 대본대로 말했다고 가정한
          잠정값이에요. 어떻게 말했는지 답해 주시면 다시 계산하며, 점수가 달라질 수 있어요.
        </p>
        <p className="text-sm leading-relaxed">
          잘 모르겠거나 건너뛰면 해당 항목은 보류로 남고 내용 점수는 잠정값을 유지해요. 보류한
          부분은 수치 정확도와 대본 충실도 계산에서 제외돼요.
        </p>
        <p role="timer" className="tabular font-mono text-xs text-dusk-accent">
          남은 시간 09:12
        </p>
        <ol className="flex w-full flex-col gap-3 text-left">
          <li className="border-2 border-dusk-line p-3">
            <p className="font-mono text-xs font-bold text-dusk-accent">3번 장 · 숫자</p>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm leading-relaxed">
              <dt className="text-xs leading-6 text-dusk-accent">대본</dt>
              <dd>
                점유율이 <mark>37%</mark>까지 올랐습니다.
              </dd>
              <dt className="text-xs leading-6 text-dusk-accent">들린 말</dt>
              <dd>
                점유율이 <mark>삼십칠 퍼센트</mark>까지 올랐습니다.
              </dd>
            </dl>
            <fieldset className="mt-3">
              <legend className="sr-only">3번 장에서 어떻게 말했나요?</legend>
              <div className="flex flex-col gap-1.5">
                {CONFIRM_OPTIONS.map((o) => (
                  <label
                    key={o.value}
                    className="flex cursor-pointer items-center gap-2 border-2 border-dusk-line px-3 py-2 text-sm has-checked:border-dusk-accent"
                  >
                    <input
                      type="radio"
                      name="confirm-sample"
                      value={o.value}
                      checked={pick === o.value}
                      onChange={() => setPick(o.value)}
                      className="accent-dusk-accent"
                    />
                    <span>{o.label}</span>
                    {o.word !== null && <span className="text-dusk-accent">‘{o.word}’</span>}
                  </label>
                ))}
              </div>
            </fieldset>
          </li>
        </ol>
        <div className="flex flex-wrap justify-center gap-3">
          <Button disabled={pick === null} onClick={back}>
            답 보내기
          </Button>
          <Button variant="secondary" onClick={back}>
            건너뛰기
          </Button>
        </div>
      </>
    );
  return <DuskScreen report={SAMPLE_REPORT}>{body}</DuskScreen>;
}
