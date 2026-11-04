import { useState } from 'react';
import { Link } from 'react-router';
import { ButtonLink } from '@/shared/ui/Button';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';

/**
 * 분석 대기 · 8비트 로딩 시안 (P6-0) — `/dev/design/processing-loading`.
 *
 * 게임 로딩 화면처럼 화면 전체가 노을 그림입니다. 배경은 확장한 그림(`public/loading/sunset-wide-bird.png`)을 쓰고
 * 움직이지 않습니다. 그림은 화면 전체를 채우고, 화면 비율에 따라 가장자리만 잘립니다.
 *
 * ★ 대화 상자 색은 시안 안에만 둡니다. 채택하면 `index.css` 의 `@theme` 에 둘지 정해야 합니다 (CLAUDE.md 7번).
 */

/** BE `takes.analysis_stage` 의 진행 중 단계 (#239) */
type Variant = 'FINALIZING' | 'SCORING' | 'AWAITING_CONFIRMATION' | 'REVIEWING';

const VARIANTS: { id: Variant; label: string }[] = [
  { id: 'FINALIZING', label: '기록 정리' },
  { id: 'SCORING', label: '내용 채점' },
  { id: 'AWAITING_CONFIRMATION', label: '대본 확인 대기' },
  { id: 'REVIEWING', label: '코치 총평' },
];

const STEP_NAMES = ['발표 기록 정리', '말한 내용 채점', '코치 총평'];

type StepState = 'DONE' | 'RUNNING' | 'WAITING' | 'PENDING';

/** 단계마다 세 칸의 상태 */
const STATES: Record<Variant, StepState[]> = {
  FINALIZING: ['RUNNING', 'PENDING', 'PENDING'],
  SCORING: ['DONE', 'RUNNING', 'PENDING'],
  AWAITING_CONFIRMATION: ['DONE', 'WAITING', 'PENDING'],
  REVIEWING: ['DONE', 'DONE', 'RUNNING'],
};

/** 실제 화면은 팁 여러 개 중 하나를 무작위로 골라 몇 초마다 바꿉니다. 시안은 하나만 둡니다 */
const SAMPLE_TIP = '문장 끝에서 고개를 들어 봐. 청중과 눈 맞추기가 훨씬 쉬워져.';

const C = {
  night: '#2B2140',
  nightLine: '#4A3A66',
  frame: '#FFF4E0',
  text: '#FFF4E0',
  accent: '#F9BE7A',
};

/** "분석 중..." 점만 움직입니다. 배경은 멈춰 있습니다 */
const CSS = `
@keyframes lp-dot { 0%, 20% { opacity: 0; } 30%, 90% { opacity: 1; } 100% { opacity: 0; } }
.lp-dot { animation: lp-dot 1.6s infinite; }
@media (prefers-reduced-motion: reduce) { .lp-dot { animation: none; } }
`;

export function LoadingDraft() {
  const [variant, setVariant] = useState<Variant>('SCORING');
  const states = STATES[variant];
  const current = states.findIndex((s) => s === 'RUNNING' || s === 'WAITING');
  const waiting = states[current] === 'WAITING';

  return (
    <div
      className="relative flex min-h-dvh flex-col overflow-hidden"
      style={{ background: C.night }}
    >
      <style>{CSS}</style>

      {/* 화면 전체를 채웁니다. 비율이 다르면 가장자리만 잘리고 중앙 장면을 유지합니다. */}
      <div className="absolute inset-0 flex items-center justify-center">
        <img
          src="/loading/sunset-wide-bird.png"
          alt="노을 진 바닷가 도시 위를 가방을 멘 치치가 날아가는 그림"
          width={1672}
          height={941}
          className="h-full w-full object-cover object-center"
        />
      </div>

      <header className="relative z-10 flex flex-wrap items-center justify-between gap-3 px-5 pt-5 sm:px-8">
        <Link to="/" aria-label="홈으로" className="border-2 border-ink bg-panel px-3 py-2">
          <PitchCoachWordmark />
        </Link>
        <p
          className="tabular px-2 py-1 text-sm font-bold"
          style={{ color: C.text, background: C.night }}
        >
          캡스톤 최종 발표 / TAKE 4 · 발표 길이 04:38
        </p>
      </header>

      {/* 대화 상자는 넓은 화면에서 오른쪽, 좁은 화면에서 아래쪽에. 그림 왼쪽의 치치를 가리지 않습니다 */}
      <main className="relative z-10 flex flex-1 items-end justify-center px-5 pb-8 pt-4 sm:px-8 lg:items-center lg:justify-end lg:pb-4">
        <Dialog waiting={waiting} states={states} current={current} />
      </main>

      <DraftTools variant={variant} onVariant={setVariant} />
    </div>
  );
}

/**
 * 게임 대화 상자 — 진한 바탕에 두 겹 테두리. 팁은 치치가 말하는 대사처럼 이름표를 붙였습니다.
 * 말풍선을 치치에 붙이지 않은 이유: 좁은 화면에서 그림이 잘리면 말풍선도 같이 잘립니다.
 */
function Dialog({
  waiting,
  states,
  current,
}: {
  waiting: boolean;
  states: StepState[];
  current: number;
}) {
  return (
    <section
      className="flex w-full max-w-xl flex-col lg:max-w-md items-center gap-4 border-4 px-5 py-5 text-center sm:px-8"
      style={{
        background: C.night,
        borderColor: C.frame,
        boxShadow: `inset 0 0 0 3px ${C.night}, inset 0 0 0 5px ${C.nightLine}`,
      }}
    >
      <p className="text-lg font-bold sm:text-xl" style={{ color: C.text }}>
        {waiting ? '확인을 기다리는 중' : '분석 중'}
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="lp-dot"
            style={{ animationDelay: `${i * 0.25}s` }}
            aria-hidden="true"
          >
            .
          </span>
        ))}
      </p>

      {/* 세 단계를 세 칸 막대로. 칸마다 이름을 읽어 주어 색만으로 구분하지 않습니다 */}
      <ol className="flex w-full max-w-md gap-1.5" aria-label="분석 단계">
        {STEP_NAMES.map((name, i) => (
          <li key={name} className="flex-1">
            <span
              className="block h-3 border-2"
              style={{
                borderColor: C.nightLine,
                background: states[i] === 'PENDING' ? 'transparent' : C.accent,
                opacity: states[i] === 'DONE' ? 0.55 : 1,
              }}
              aria-hidden="true"
            />
            <span className="sr-only">
              {name} {stateLabel(states[i]!)}
            </span>
          </li>
        ))}
      </ol>
      <p className="font-mono text-xs" style={{ color: C.accent }}>
        {current + 1}/3 · {STEP_NAMES[current]}
      </p>

      {/* 확인 대기 — 실제 화면은 확인 화면(/takes/:takeId/confirm)으로 가는 버튼을 띄웁니다 */}
      {waiting && (
        <>
          <p className="text-sm leading-relaxed" style={{ color: C.text }}>
            비슷하게 들린 말이 있어요. 어떻게 말했는지 알려 주면 채점에 반영되고, 답이 없으면 10분
            안에 그대로 진행돼요.
          </p>
          <ButtonLink to="/takes/t-confirm/confirm">비슷하게 들린 말 확인하기</ButtonLink>
        </>
      )}

      <div
        className="w-full border-t-2 border-dashed pt-4 text-left"
        style={{ borderColor: C.nightLine }}
      >
        <p className="font-mono text-xs font-bold" style={{ color: C.accent }}>
          TIP · 치치
        </p>
        <p className="mt-1 text-sm leading-relaxed" style={{ color: C.text }}>
          {SAMPLE_TIP}
        </p>
      </div>

      <p className="text-xs" style={{ color: C.accent }}>
        {!waiting && '보통 1분 안에 끝나요. '}화면을 떠나도 분석은 계속돼요.
      </p>
    </section>
  );
}

/** 시안 검토 도구 — 전체 화면이라 `DraftPage` 대신 구석에 접어 둡니다 */
function DraftTools({ variant, onVariant }: { variant: Variant; onVariant: (v: Variant) => void }) {
  return (
    <details className="fixed bottom-3 right-3 z-20 max-w-xs border-2 border-ink bg-panel p-3 text-sm text-ink">
      <summary className="cursor-pointer font-bold">시안 검토 도구</summary>
      <div className="mt-3 flex flex-col gap-3">
        <div className="flex flex-wrap gap-1.5">
          {VARIANTS.map((v) => (
            <button
              key={v.id}
              type="button"
              aria-pressed={variant === v.id}
              onClick={() => onVariant(v.id)}
              className={[
                'px-2 py-1 text-xs font-bold',
                variant === v.id ? 'bg-ink text-panel' : 'bg-cream text-stone hover:text-ink',
              ].join(' ')}
            >
              {v.label}
            </button>
          ))}
        </div>
        <ul className="list-disc space-y-1 pl-4 text-xs text-stone">
          <li>배경은 화면을 꽉 채워요. 화면 비율에 따라 가장자리가 조금 잘릴 수 있어요.</li>
          <li>
            대화 상자 색은 시안 안에만 있어요. 채택하면 index.css 의 @theme 에 둘지 정해야 해요.
          </li>
          <li>전체 화면이라 로그인 헤더(이름 · 로그아웃)를 이 화면에서 숨길지 정해야 해요.</li>
        </ul>
        <Link to="/dev/design" className="text-xs font-bold underline">
          시안 목록으로
        </Link>
      </div>
    </details>
  );
}

function stateLabel(state: StepState) {
  switch (state) {
    case 'DONE':
      return '끝남';
    case 'RUNNING':
      return '진행 중';
    case 'WAITING':
      return '확인 대기';
    default:
      return '대기';
  }
}
