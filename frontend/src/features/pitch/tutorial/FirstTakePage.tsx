import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { pitchListKey } from '@/shared/api/home';
import { toMessage } from '@/shared/api/errorMessage';
import { MAIN_ID } from '@/shared/ui/SkipLink';
import { FIRST_TAKE_SLIDES, FIRST_TAKE_TITLE } from './firstTakeMaterials';
import { ensureFirstTakePitch } from './firstTakeSetup';
import { FirstTakeError } from './firstTakeError';

/** #931: 진입 안내와 샘플 자동 준비. 실제 리허설·Take 생성 로직은 건드리지 않습니다. */
export function FirstTakePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [repairPitchId, setRepairPitchId] = useState<string | null>(null);
  const busyRef = useRef(false);
  const prepareControllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => prepareControllerRef.current?.abort(), []);

  const begin = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    const controller = new AbortController();
    prepareControllerRef.current = controller;
    setPending(true);
    setError(null);
    setRepairPitchId(null);
    setStatus('');

    try {
      const pitchId = await ensureFirstTakePitch((message) => {
        if (!controller.signal.aborted) setStatus(message);
      }, controller.signal);
      if (!controller.signal.aborted) {
        void queryClient.invalidateQueries({ queryKey: pitchListKey }).catch(() => undefined);
        navigate(`/pitch/${pitchId}/device-check`);
      }
    } catch (caught) {
      if (!controller.signal.aborted) {
        setStatus('');
        setError(caught instanceof FirstTakeError ? caught.message : toMessage(caught));
        setRepairPitchId(caught instanceof FirstTakeError ? (caught.pitchId ?? null) : null);
      }
    } finally {
      if (prepareControllerRef.current === controller) prepareControllerRef.current = null;
      busyRef.current = false;
      if (!controller.signal.aborted) setPending(false);
    }
  };

  return (
    <main
      id={MAIN_ID}
      tabIndex={-1}
      className="pitch-create flex flex-1 flex-col bg-greige px-6 py-10 text-ink lg:px-10"
    >
      <div className="mx-auto w-full max-w-4xl">
        <p className="text-xs font-bold tracking-widest text-coral-deep">
          PITCHCOACH · FIRST MISSION
        </p>
        <h1 className="mt-4 text-3xl font-bold">치치와 함께하는 첫 번째 Take</h1>
        <p className="mt-4 text-sm leading-7 text-stone">
          별도의 PDF나 대본 없이 피치코치를 체험해 보세요. 준비된 발표를 직접 읽으며 슬라이드
          이동부터 실시간 코칭, 결과 리포트까지 익힙니다.
        </p>

        <section className="mt-8 border-2 border-ink bg-panel p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-bold">{FIRST_TAKE_TITLE}</h2>
            <span className="border border-line-strong bg-cream px-3 py-1 text-xs font-bold">
              5 SLIDES · 목표 3분
            </span>
          </div>
          <ol className="mt-5 grid gap-3 sm:grid-cols-2">
            {FIRST_TAKE_SLIDES.map((slide) => (
              <li key={slide.mission} className="border border-line-strong bg-white p-4">
                <p className="text-xs font-bold text-coral-deep">{slide.mission}</p>
                <p className="mt-2 text-sm font-bold">{slide.title}</p>
                <p className="mt-1 text-xs leading-6 text-stone">{slide.subtitle}</p>
              </li>
            ))}
          </ol>
          <p className="mt-6 border-t border-line-strong pt-4 text-sm leading-7">
            샘플 발표는 내 피치에 저장되며, 발표 기록도 남습니다. 카메라 영상은 브라우저에서
            처리하고, 발표 음성은 분석을 위해 서버로 전송됩니다. 코칭 결과는 실제 측정에 따라
            달라지며 예시 점수나 가짜 피드백을 사용하지 않습니다.
          </p>
        </section>

        <div className="mt-7 flex flex-wrap items-center gap-4">
          <button
            type="button"
            aria-disabled={pending}
            onClick={() => void begin()}
            className="border-2 border-ink bg-coral-deep px-6 py-3 text-sm font-bold text-white shadow-[4px_4px_0_var(--color-ink)] hover:bg-ink aria-disabled:cursor-wait aria-disabled:opacity-60"
          >
            {pending ? '샘플 준비 중…' : '첫 미션 시작하기 →'}
          </button>
          <Link to="/" className="text-sm font-bold underline underline-offset-4">
            내 피치로 돌아가기
          </Link>
        </div>
        <p role="status" className={status ? 'mt-5 text-sm text-stone' : 'sr-only'}>
          {status}
        </p>
        {error && (
          <div className="mt-3 space-y-2">
            <p role="alert" className="text-sm font-bold text-coral-deep">
              {error}
            </p>
            {repairPitchId && (
              <Link
                to={'/pitch/' + encodeURIComponent(repairPitchId) + '/edit'}
                className="inline-block text-sm font-bold underline underline-offset-2"
              >
                샘플 피치에서 수정하기
              </Link>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
