import { useEffect, useState, type ReactNode } from 'react';
import { Link, Navigate, useNavigate, useParams } from 'react-router';
import { isSettled, useTakeReport } from '@/shared/api/report';
import { toMessage } from '@/shared/api/errorMessage';
import { formatDuration } from '@/shared/lib/clock';
import { ButtonLink } from '@/shared/ui/Button';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';
import { stepStates, type StepState } from './reportFormat';

/**
 * 분석 대기 (P6-0) — 발표를 끝낸 뒤 리포트가 나오기 전. 게임 로딩 화면처럼 화면 전체가 노을 그림입니다.
 *
 * Take 리포트를 끝날 때까지 주기적으로 묻고, 끝나면 리포트로 넘깁니다. 리뷰가 실패해도 측정값은 있어
 * 리포트로 넘어가고, 분석 자체가 실패했을 때만 여기 남습니다. 화면을 떠나도 분석은 서버에서 이어집니다.
 *
 * 배경 그림(`public/loading/sunset-wide-bird.png`)은 움직이지 않고 화면을 채웁니다 — 비율이 다르면 가장자리만 잘립니다.
 * 상태 · 진행 · 팁은 그림 위 대화 상자에 둡니다. 넓은 화면은 오른쪽, 좁은 화면은 아래쪽이라 왼쪽의 치치를 가리지 않습니다.
 *
 * ★ 대본 확인(`AWAITING_CONFIRMATION`)은 기다림만 보여 줍니다. 답하는 화면은 따로 만듭니다 —
 *   답이 없어도 BE 가 정한 시간이 지나면 그대로 진행됩니다.
 */

const STEP_NAMES = ['발표 기록 정리', '말한 내용 채점', '코치 총평'];

const STEP_LABEL: Record<StepState, string> = {
  DONE: '끝남',
  RUNNING: '진행 중',
  WAITING: '확인 대기',
  PENDING: '대기',
};

// 확인 화면이 아직 없어 답할 수 없습니다. BE 는 확인을 최대 10분 기다립니다 (`architecture.md` 1-3)
const WAITING_DETAIL =
  '비슷하게 들린 말이 있어 확인을 기다려요. 답이 없으면 10분 안에 그대로 진행돼요.';

/**
 * 기다리는 동안 치치가 건네는 팁 — 게임 로딩 화면의 Tip 처럼 몇 초마다 바뀝니다.
 * 제품 설명은 실제 동작과 맞아야 합니다 (CLAUDE.md 1번 · 8번 · 핵심 철학).
 */
const TIPS = [
  '문장 끝에서 고개를 들어 봐. 청중과 눈 맞추기가 훨씬 쉬워져.',
  '숫자를 말할 땐 한 박자 쉬어 봐. 듣는 사람이 기억하기 쉬워.',
  '"음", "어" 대신 잠깐 멈추면 더 자신 있어 보여.',
  '다음 Take에선 미션 하나만 신경 써 봐. 한꺼번에 다 고치려면 더 어려워.',
  '리포트는 점수가 아니라 지난 Take보다 뭐가 나아졌는지 보여 줘.',
  '대본 보기를 "핵심 키워드만"으로 바꾸면 대본에서 벗어나는 연습이 돼.',
  '카메라 영상은 서버로 안 가. 시선은 브라우저 안에서만 계산해.',
];

const TIP_MS = 6000;

/** 지금 팁을 뺀 나머지 중 하나를 고릅니다 — 같은 팁이 연달아 나오지 않게 */
function otherTip(current: number) {
  return (current + 1 + Math.floor(Math.random() * (TIPS.length - 1))) % TIPS.length;
}

/** 팁 번호. 매번 앞부분만 보지 않게 처음 팁도, 다음 팁도 무작위로 고릅니다 */
function useRotatingTip() {
  const [index, setIndex] = useState(() => Math.floor(Math.random() * TIPS.length));
  useEffect(() => {
    const id = window.setInterval(() => setIndex(otherTip), TIP_MS);
    return () => window.clearInterval(id);
  }, []);
  return index;
}

export function ProcessingPage() {
  const { takeId = '' } = useParams();
  const report = useTakeReport(takeId);
  const navigate = useNavigate();
  const data = report.data;
  const failed = data?.status === 'FAILED';
  const held = Boolean(data?.analysis_hold_reason);
  const settled = isSettled(data);

  useEffect(() => {
    if (settled && !failed && !held) navigate(`/takes/${takeId}`, { replace: true });
  }, [settled, failed, held, navigate, takeId]);

  if (!takeId) return <Navigate replace to="/" />;

  return (
    <div className="relative flex min-h-dvh flex-col overflow-hidden bg-dusk">
      <img
        src="/loading/sunset-wide-bird.png"
        alt="노을 진 바닷가 도시 위를 가방을 멘 치치가 날아가는 그림"
        width={1672}
        height={941}
        className="absolute inset-0 h-full w-full object-cover object-center"
      />

      <header className="relative z-10 flex flex-wrap items-center justify-between gap-3 px-5 pt-5 sm:px-8">
        <Link to="/" aria-label="홈으로" className="border-2 border-ink bg-panel px-3 py-2">
          <PitchCoachWordmark />
        </Link>
        {data && (
          <p className="tabular bg-dusk px-2 py-1 text-sm font-bold text-dusk-ink">
            {data.pitch_title} / TAKE {data.take_number}
            {data.measures && ` · 발표 길이 ${formatDuration(data.measures.duration_ms)}`}
          </p>
        )}
      </header>

      <main className="relative z-10 flex flex-1 items-end justify-center px-5 pb-8 pt-4 sm:px-8 lg:items-center lg:justify-end lg:pb-4">
        <DialogBox>
          <DialogContent
            isPending={report.isPending}
            error={report.error}
            failed={failed}
            held={held}
            pitchId={data?.pitch_id}
            states={stepStates(data?.analysis_stage ?? null)}
          />
        </DialogBox>
      </main>
    </div>
  );
}

/** 게임 대화 상자 — 진한 바탕에 두 겹 테두리 */
function DialogBox({ children }: { children: ReactNode }) {
  return (
    <section className="flex w-full max-w-xl flex-col items-center gap-4 border-4 border-dusk-ink bg-dusk px-5 py-5 text-center text-dusk-ink shadow-[inset_0_0_0_3px_var(--color-dusk),inset_0_0_0_5px_var(--color-dusk-line)] sm:px-8 lg:max-w-md">
      {children}
    </section>
  );
}

function DialogContent({
  isPending,
  error,
  failed,
  held,
  pitchId,
  states,
}: {
  isPending: boolean;
  error: Error | null;
  failed: boolean;
  held: boolean;
  pitchId: string | undefined;
  states: StepState[];
}) {
  if (error)
    return (
      <div role="alert" className="flex flex-col items-center gap-4">
        <p className="text-lg font-bold">연습 기록을 불러오지 못했어요.</p>
        <p className="text-sm">{toMessage(error)}</p>
        <ButtonLink to="/" variant="secondary">
          홈으로
        </ButtonLink>
      </div>
    );

  if (isPending) return <StatusLine text="연습 기록을 불러오는 중" />;

  if (failed)
    return (
      <div role="alert" className="flex flex-col items-center gap-4">
        <p className="text-lg font-bold">이번 Take는 분석하지 못했어요</p>
        <p className="text-sm leading-relaxed">
          발표 기록이 너무 짧거나 측정이 거의 되지 않았을 수 있어요. 이번 Take는 연습 기록에
          &apos;분석 실패&apos;로 남아요.
        </p>
        <div className="flex flex-wrap justify-center gap-3">
          {pitchId && <ButtonLink to={`/pitch/${pitchId}/device-check`}>다시 연습하기</ButtonLink>}
          <ButtonLink to="/" variant="secondary">
            홈으로
          </ButtonLink>
        </div>
      </div>
    );

  // 리뷰 보류는 사람이 복구할 때까지 그대로라, 기다리라고 하지 않습니다
  if (held)
    return (
      <div role="alert" className="flex flex-col items-center gap-4">
        <p className="text-lg font-bold">분석이 잠시 멈췄어요</p>
        <p className="text-sm leading-relaxed">
          말한 내용을 채점하다 문제가 생겨 확인하고 있어요. 바로 끝나지 않으니 홈에서 나중에 다시
          확인해 주세요.
        </p>
        <ButtonLink to="/" variant="secondary">
          홈으로
        </ButtonLink>
      </div>
    );

  return <Progress states={states} />;
}

/** "분석 중..." — 점 셋이 차례로 켜집니다. 점은 화면 읽기 프로그램이 읽지 않습니다 */
function StatusLine({ text }: { text: string }) {
  return (
    <p role="status" className="text-lg font-bold sm:text-xl">
      {text}
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="loading-dot"
          style={{ animationDelay: `${i * 0.25}s` }}
          aria-hidden="true"
        >
          .
        </span>
      ))}
    </p>
  );
}

function Progress({ states }: { states: StepState[] }) {
  const tip = useRotatingTip();
  const current = states.findIndex((s) => s === 'RUNNING' || s === 'WAITING');
  const waiting = states[current] === 'WAITING';

  return (
    <>
      <StatusLine text={waiting ? '확인을 기다리는 중' : '분석 중'} />

      {/* 세 단계를 세 칸 막대로. 칸마다 이름을 읽어 주어 색만으로 구분하지 않습니다 */}
      <ol className="flex w-full max-w-md gap-1.5" aria-label="분석 단계">
        {STEP_NAMES.map((name, i) => (
          <li key={name} className="flex-1">
            <span
              aria-hidden="true"
              className={[
                'block h-3 border-2 border-dusk-line',
                states[i] === 'PENDING' ? '' : 'bg-dusk-accent',
                states[i] === 'DONE' ? 'opacity-55' : '',
              ].join(' ')}
            />
            <span className="sr-only">
              {name} {STEP_LABEL[states[i]!]}
            </span>
          </li>
        ))}
      </ol>
      {current >= 0 && (
        <p aria-live="polite" className="font-mono text-xs text-dusk-accent">
          {current + 1}/3 · {STEP_NAMES[current]}
        </p>
      )}
      {waiting && <p className="text-sm">{WAITING_DETAIL}</p>}

      {/* 몇 초마다 바뀌어 aria-live 를 걸지 않습니다 — 화면 읽기 프로그램이 계속 읽게 됩니다 */}
      <div className="w-full border-t-2 border-dashed border-dusk-line pt-4 text-left">
        <p className="font-mono text-xs font-bold text-dusk-accent">TIP · 치치</p>
        <p className="mt-1 text-sm leading-relaxed">{TIPS[tip]}</p>
      </div>

      <p className="text-xs text-dusk-accent">
        {!waiting && '보통 1분 안에 끝나요. '}화면을 떠나도 분석은 계속되고, 홈에서 다시 볼 수
        있어요.
      </p>
      <ButtonLink to="/" variant="secondary" size="sm">
        홈으로
      </ButtonLink>
    </>
  );
}
