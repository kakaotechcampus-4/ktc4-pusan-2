// Draft #1105 First Take 화면의 정적 설계서 샘플. 실제 API·PDF 생성은 실행하지 않는다.
import type { Navigate } from './shared';

const MISSION_CARDS = [
  ['MISSION 01', '첫 번째 Take, 시작합니다', '완벽한 발표가 아닌 첫 연습의 완수가 목표예요.'],
  ['MISSION 02', '슬라이드가 바뀌면, 대본도 함께', '현재 장에 연결된 대본을 보면서 발표해 보세요.'],
  ['MISSION 03', '발표 중에 확인할 세 가지', '말하는 동안에도 발표 흐름을 살펴볼 수 있어요.'],
  ['MISSION 04', '코칭은 힌트, 발표는 내가', '코칭을 기다리지 말고 이야기를 이어 가세요.'],
  ['MISSION 05', '첫 미션을 마무리할 시간', '끝까지 연습한 뒤 실제 분석 결과를 확인해 보세요.'],
] as const;

export function TutorialScreen({
  stateId,
  onNavigate,
}: {
  stateId: string;
  onNavigate: Navigate;
}) {
  const pending = stateId === 'pending';
  const error = stateId === 'error';

  return (
    <div className="pitch-create flex min-h-dvh flex-col bg-greige px-6 py-10 text-ink lg:px-10">
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
            <h2 className="text-lg font-bold">[튜토리얼] 치치와 함께하는 First Take</h2>
            <span className="border border-line-strong bg-cream px-3 py-1 text-xs font-bold">
              5 SLIDES · 목표 3분
            </span>
          </div>
          <ol className="mt-5 grid gap-3 sm:grid-cols-2">
            {MISSION_CARDS.map(([mission, title, subtitle]) => (
              <li key={mission} className="border border-line-strong bg-white p-4">
                <p className="text-xs font-bold text-coral-deep">{mission}</p>
                <p className="mt-2 text-sm font-bold">{title}</p>
                <p className="mt-1 text-xs leading-6 text-stone">{subtitle}</p>
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
            disabled={pending}
            onClick={() => onNavigate('prepare-mission')}
            className="border-2 border-ink bg-coral-deep px-6 py-3 text-sm font-bold text-white shadow-[4px_4px_0_var(--color-ink)] hover:bg-ink disabled:cursor-wait disabled:opacity-60"
          >
            {pending ? '샘플 준비 중…' : '첫 미션 시작하기 →'}
          </button>
          <button
            type="button"
            onClick={() => onNavigate('home')}
            className="text-sm font-bold underline underline-offset-4"
          >
            내 피치로 돌아가기
          </button>
        </div>
        {pending && (
          <p role="status" className="mt-5 text-sm text-stone">
            샘플 발표를 준비하고 있습니다. (설계서 예시)
          </p>
        )}
        {error && (
          <p role="alert" className="mt-3 text-sm font-bold text-coral-deep">
            요청을 처리하지 못했어요. 잠시 뒤 다시 시도해 주세요.
          </p>
        )}
        <p className="mt-6 text-xs text-stone">
          설계서 전용 미리보기입니다. 실제 샘플 저장·폴링·PDF 생성 없이 장치 점검 화면으로 연결합니다.
        </p>
      </div>
    </div>
  );
}
