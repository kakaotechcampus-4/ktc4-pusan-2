// Draft #1105 First Take 튜토리얼 준비 흐름의 독립 디자인 샘플. API·PDF 생성은 실행하지 않는다.
import { useState } from 'react';
import type { Navigate } from './shared';

const STEPS = ['샘플 발표 찾기', '5장 슬라이드 준비', '대본 연결 확인', '장치 점검으로 이동'];

export function TutorialScreen({ onNavigate }: { onNavigate: Navigate }) {
  const [step, setStep] = useState(0);
  const [failed, setFailed] = useState(false);
  return (
    <main className="min-h-dvh bg-greige px-6 py-12 text-ink">
      <div className="mx-auto max-w-3xl border-2 border-ink bg-panel p-6 shadow-[4px_4px_0_var(--color-ink)] sm:p-10">
        <p className="font-mono text-xs font-bold tracking-widest text-coral-deep">FIRST TAKE / TUTORIAL</p>
        <h1 className="mt-4 text-3xl font-extrabold">첫 미션 체험하기</h1>
        <p className="mt-4 text-sm leading-7">자료를 직접 올리지 않아도 샘플 발표로 서비스 사용방법을 익힐 수 있어요. 실제 서비스에서는 샘플 PDF와 대본을 자동으로 준비해요.</p>
        <ol className="mt-8 space-y-3" aria-label="샘플 발표 준비 단계">
          {STEPS.map((name, index) => (
            <li key={name} className={`flex items-center gap-3 border p-4 ${index === step ? 'border-ink bg-coral-wash' : 'border-line-strong'}`}>
              <span className="font-mono font-bold">{index + 1}/4</span>
              <span className="flex-1 font-bold">{name}</span>
              <span className="text-xs">{index < step ? '완료' : index === step ? '진행 중' : '대기'}</span>
            </li>
          ))}
        </ol>
        {failed && <p role="alert" className="mt-5 border border-coral-deep bg-coral-wash p-3 text-sm">샘플 준비에 실패했어요. 이전 단계부터 다시 시도할 수 있어요.</p>}
        <div className="mt-8 flex flex-wrap gap-3">
          <button type="button" className="border border-ink px-5 py-3" onClick={() => onNavigate('home')}>홈으로 돌아가기</button>
          <button type="button" className="border-2 border-ink bg-coral-deep px-5 py-3 font-bold text-white" onClick={() => {
            if (step === STEPS.length - 1) onNavigate('prepare-mission');
            else setStep((current) => current + 1);
            setFailed(false);
          }}>{step === STEPS.length - 1 ? '장치 점검으로 이동' : '다음 준비 단계'}</button>
          <button type="button" className="text-sm underline" onClick={() => setFailed(true)}>오류 상태 보기</button>
        </div>
        <p className="mt-6 text-xs text-ink/70">설계서 전용 미리보기: 실제 업로드·폴링·중복 피치 재사용은 수행하지 않아요.</p>
      </div>
    </main>
  );
}
