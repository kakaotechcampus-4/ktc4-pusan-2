import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { pitchListKey } from '@/shared/api/home';
import { startFirstTake } from '@/shared/api/pitch';
import { toMessage } from '@/shared/api/errorMessage';
import { Button, ButtonLink } from '@/shared/ui/Button';
import { MAIN_ID } from '@/shared/ui/SkipLink';
import { FIRST_TAKE_SLIDES } from './firstTakeMaterials';

/**
 * #931: First Take 튜토리얼 안내와 진입. 샘플 피치는 BE 가 준비된 자료로 만들어 줍니다(#1135) —
 * 대본 나누기 · 평가 기준이 끝난 상태라 받자마자 장치 점검으로 갑니다. Take 는 거기서 평소처럼 생깁니다.
 *
 * 튜토리얼 리포트는 계정당 한 번입니다. 이미 받았으면 장치 점검으로 보내지 않고 여기서 알립니다 —
 * 보내도 시작 버튼이 409 `TUTORIAL_REPORT_USED` 로 막힙니다.
 */
export function FirstTakePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const start = useMutation({
    mutationFn: startFirstTake,
    // 처음이면 홈 목록에 샘플 피치가 새로 생깁니다. 화면을 떠났어도 목록은 맞춥니다.
    // 이동을 목록 갱신에 묶지 않으려고 기다리지 않습니다
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: pitchListKey }).catch(() => undefined);
    },
  });
  // 리포트를 이미 받았을 때. 샘플 피치가 남아 있으면 그 기록으로 가는 길도 보여 줍니다(지웠으면 null)
  const [used, setUsed] = useState<{ pitchId: string | null } | null>(null);

  const begin = () => {
    setUsed(null);
    // mutate 에 준 콜백은 화면을 떠난 뒤에는 불리지 않습니다 — 떠난 사용자를 장치 점검으로 끌고 가지 않습니다
    start.mutate(undefined, {
      onSuccess: ({ pitch_id, report_received }) => {
        if (report_received || pitch_id === null) setUsed({ pitchId: pitch_id });
        else navigate(`/pitch/${encodeURIComponent(pitch_id)}/device-check`);
      },
    });
  };

  return (
    <main
      id={MAIN_ID}
      tabIndex={-1}
      className="pitch-create flex flex-1 flex-col bg-greige px-6 py-10 text-ink lg:px-10"
    >
      <div className="mx-auto w-full max-w-4xl">
        <p className="text-xs font-bold tracking-widest text-coral-deep">PITCHCOACH · TUTORIAL</p>
        <h1 className="mt-4 text-3xl font-bold">치치와 함께하는 첫 번째 Take</h1>
        <p className="mt-4 text-sm leading-7 text-stone">
          별도의 PDF나 대본 없이 피치코치를 체험해 보세요. 준비된 발표를 직접 읽으며 슬라이드
          이동부터 실시간 코칭, 결과 리포트까지 익힙니다.
        </p>
        <p className="mt-1 text-sm leading-7 text-stone">
          튜토리얼 리포트는 계정당 한 번 받을 수 있어요. 리포트가 나오기 전에 연습이 끊겼다면 다시
          할 수 있어요.
        </p>

        {/* 시작 버튼과 그 결과(안내 · 오류)는 소개 바로 아래에 둡니다. 팀 기준 화면(1366×650)에서
            샘플 장 목록 아래에 두면 버튼과 결과가 첫 화면 밖으로 밀려, 눌러도 반응이 없어 보입니다 */}
        <div className="mt-6 flex flex-wrap items-center gap-4">
          <Button aria-disabled={start.isPending} onClick={begin}>
            {start.isPending ? '샘플 준비 중…' : '튜토리얼 시작하기 →'}
          </Button>
          <Link to="/" className="text-sm font-bold underline underline-offset-4">
            내 피치로 돌아가기
          </Link>
        </div>
        <p role="status" className={start.isPending ? 'mt-5 text-sm text-stone' : 'sr-only'}>
          {start.isPending ? '샘플 발표를 준비하고 있어요.' : ''}
        </p>
        {start.isError && (
          <p role="alert" className="mt-3 text-sm font-bold text-coral-deep">
            샘플 발표를 준비하지 못했어요. {toMessage(start.error)}
          </p>
        )}
        {used && (
          <section
            role="alert"
            aria-labelledby="first-take-used"
            className="mt-6 border-2 border-ink bg-cream p-5"
          >
            <h2 id="first-take-used" className="font-bold">
              튜토리얼 리포트를 이미 받았어요
            </h2>
            <p className="mt-2 text-sm leading-7">
              샘플 발표로는 더 연습할 수 없어요.{' '}
              {used.pitchId && '지난 연습은 Take 기록에서 볼 수 있고, '}내 자료로 새 피치를 만들면
              계속 연습할 수 있어요.
            </p>
            <div className="mt-4 flex flex-wrap gap-3">
              <ButtonLink to="/pitch/new" size="sm">
                새 피치 만들기
              </ButtonLink>
              {used.pitchId && (
                <ButtonLink
                  to={`/takes?pitch=${encodeURIComponent(used.pitchId)}`}
                  variant="secondary"
                  size="sm"
                >
                  샘플 Take 기록 보기
                </ButtonLink>
              )}
            </div>
          </section>
        )}
        <section className="mt-10 border-2 border-ink bg-panel p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-bold">샘플 발표</h2>
            <span className="border border-line-strong bg-cream px-3 py-1 text-xs font-bold">
              {FIRST_TAKE_SLIDES.length} SLIDES · 목표 3분
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
            샘플 발표는 내 피치 목록에 &lsquo;샘플&rsquo;로 저장되고, 발표 기록도 남습니다. 카메라
            영상은 브라우저에서 처리하고, 발표 음성은 분석을 위해 서버로 전송됩니다. 코칭 결과는
            실제 측정에 따라 달라지며 예시 점수나 가짜 피드백을 사용하지 않습니다.
          </p>
        </section>
      </div>
    </main>
  );
}
