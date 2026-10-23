import { useState } from 'react';
import { Link } from 'react-router';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';

/**
 * 분석 대기 · 8비트 로딩 시안 (P6-0) — `/dev/design/processing-loading`.
 *
 * 게임 로딩 화면처럼 화면 전체가 노을입니다. 치치가 노을 하늘을 날아가고, 구름 · 땅 · 울타리가 뒤로 흘러갑니다.
 * 장면은 400×150 격자의 SVG 픽셀 그림이고(`PitchCoachWordmark` 와 같은 방식), 화면 아래에 붙여 양옆을 잘라 씁니다.
 * 장면 위로 남는 하늘은 같은 띠 색을 이어 칠한 배경입니다 — 세로로 긴 화면일수록 하늘이 넓어집니다.
 *
 * ★ 노을 색은 시안 안에만 둡니다. 채택하면 `index.css` 의 `@theme` 에 둘지 정해야 합니다 (CLAUDE.md 7번).
 * ★ 치치 날갯짓 두 장은 시안용으로 그린 30×22 픽셀입니다. 마스코트 원화와 맞추려면 다시 그려야 합니다.
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

/** 노을 팔레트 — 장면 안의 하늘 띠. 위에서 아래로, 띠마다 색을 끊어 8비트 느낌을 냅니다 */
const SKY = [
  { y: 0, h: 36, fill: '#7F6FB8' },
  { y: 36, h: 16, fill: '#9E7CC0' },
  { y: 52, h: 12, fill: '#C688B8' },
  { y: 64, h: 10, fill: '#E896A6' },
  { y: 74, h: 9, fill: '#F4A88C' },
  { y: 83, h: 7, fill: '#F9BE7A' },
  { y: 90, h: 6, fill: '#FCD48A' },
];

/** 장면 위로 이어지는 하늘. 마지막 띠가 장면 맨 위 띠(#7F6FB8)와 같아 이음매가 띠 하나로 보입니다 */
const UPPER_SKY =
  'linear-gradient(to bottom, #3A3172 0 16%, #4A3F88 16% 30%, #61559E 30% 42%, #7F6FB8 42% 100%)';

const C = {
  sun: '#FFE9A8',
  sunRing: '#FFD27A',
  farHill: '#9A6E9E',
  nearHill: '#6E5584',
  sea: '#E7A1A6',
  seaDeep: '#D48C9C',
  glint: '#FFE3A0',
  cloud: '#FFD7C8',
  cloudShade: '#EFA8B2',
  grass: '#7DB24A',
  grassLight: '#A2CF5E',
  grassDark: '#5E9638',
  dirt: '#9A6A45',
  dirtDark: '#7B5034',
  dirtLight: '#B5845A',
  wood: '#6B4A2E',
  woodLight: '#8E6A44',
  wind: '#FFF4E0',
  star: '#FFF4E0',
  night: '#2B2140',
  nightLine: '#4A3A66',
  frame: '#FFF4E0',
  text: '#FFF4E0',
  accent: '#F9BE7A',
};

/** 치치 스프라이트 색. 글자 하나가 픽셀 하나입니다 */
const CHICHI_COLORS: Record<string, string> = {
  K: '#3B2A20', // 외곽선
  c: '#F8E7D6', // 얼굴
  C: '#E8CEB8', // 얼굴 그늘
  b: '#966240', // 머리 갈색
  B: '#B27C54', // 날개
  d: '#603E2A', // 날개 깃
  w: '#FFFFFF', // 스웨터
  g: '#CECED8', // 스웨터 그늘
  e: '#1E1A18', // 눈
  h: '#FFFFFF', // 눈 반짝임
  p: '#F2A0A0', // 볼
  y: '#6E5C58', // 부리
  o: '#F2A23A', // 발
  t: '#463832', // 꼬리
  T: '#6E5C52', // 꼬리 윗깃
};

const WING_UP = [
  '.....KdKdKdK..................',
  '.....KBdBdBdK.................',
  '.....KBBBBBBdK................',
  '......KBBBbBBdK...KKK.........',
  '.......KBBBbBBdKKKbbbKKK......',
  '........KBBBbBBdbbbbbbbbK.....',
  '.........KBBBBBdbbbbcccccK....',
  '..........KBBBBdcbbbccccccK...',
  '...........KddddcbbcchecccK...',
  '.KKKK......KCCCcccccceeccccKK.',
  'KTTTTKKKK..KCCCccccccccccccyyK',
  'KttttTTTTKKKCCCcccccccppcccyK.',
  '.KKKKttttTTTwwgggggggggggwwK..',
  '.....KKKttttwwwwwwwwwwwwwwwK..',
  '........KttttwwwwwwwwwwwwwK...',
  '.........KKKKwwwwwwwwwwwwwK...',
  '.............KwwwwwwwwwwwK....',
  '..............KgggggggggK.....',
  '...............KKKwwwKKK......',
  '.................KoKKoK.......',
  '..................K..K........',
];

const WING_DOWN = [
  '..............................',
  '..............................',
  '..............................',
  '..................KKK.........',
  '...............KKKbbbKKK......',
  '..............KbbbbbbbbbK.....',
  '.............KbbbbbbcccccK....',
  '............KCCccbbbccccccK...',
  '............KCCccbbcchecccK...',
  '.KKKK......KCCCcccccceeccccKK.',
  'KTTTTKKKK..KCCCccccccccccccyyK',
  'KttttTTTTKKBdddddcccccppcccyK.',
  '.KKKKttttTBBBbBBdggggggggwwK..',
  '.....KKKtBBBbBBBdwwwwwwwwwwK..',
  '.......KBBBbBBBdwwwwwwwwwwK...',
  '......KBBBBBBddwwwwwwwwwwwK...',
  '......KBdBdBdKwwwwwwwwwwwK....',
  '......KdKdKdK.KgggggggggK.....',
  '.......K.K.K...KKKwwwKKK......',
  '.................KoKKoK.......',
  '..................K..K........',
];

/** 문자열 격자를 path 로. `on` 글자 자리마다 s×s 칸을 칠합니다 */
function pixels(rows: string[], on: string, ox: number, oy: number, s = 1) {
  return rows
    .flatMap((row, y) =>
      [...row].flatMap((ch, x) =>
        ch === on ? [`M${ox + x * s} ${oy + y * s}h${s}v${s}h-${s}z`] : [],
      ),
    )
    .join('');
}

/** 스프라이트 한 장을 색마다 path 하나로 */
function Sprite({ rows }: { rows: string[] }) {
  return (
    <>
      {Object.entries(CHICHI_COLORS).map(([ch, fill]) => (
        <path key={ch} d={pixels(rows, ch, 0, 0)} fill={fill} />
      ))}
    </>
  );
}

const CLOUD_BIG = [
  '00000111000000',
  '00011111110000',
  '01111111111100',
  '11111111111111',
  '02222222222220',
];
const CLOUD_SMALL = ['0011100000', '0111111100', '1111111111', '0222222220'];

/** 구름 셋. 한 벌을 400 옆에 한 번 더 그려 -400 만큼 흘리면 이어집니다 */
const CLOUDS = [
  { rows: CLOUD_BIG, x: 40, y: 40, s: 3 },
  { rows: CLOUD_SMALL, x: 190, y: 62, s: 2 },
  { rows: CLOUD_BIG, x: 300, y: 30, s: 2 },
];

function cloudPath(on: string, shift: number) {
  return CLOUDS.map((c) => pixels(c.rows, on, c.x + shift, c.y, c.s)).join('');
}

/** 계단 모양 산등성이. 10칸마다 높이 하나 */
function ridge(heights: number[], bottom: number) {
  return heights.map((h, i) => `M${i * 10} ${h}h10V${bottom}h-10z`).join('');
}

// 해(x 250) 뒤는 산을 낮춰 해가 가리지 않게 둡니다
const FAR_HILLS = ridge(
  [
    84, 82, 80, 80, 82, 85, 87, 85, 83, 81, 79, 78, 79, 81, 84, 86, 88, 89, 90, 92, 93, 94, 95, 96,
    96, 96, 96, 95, 94, 93, 90, 87, 85, 84, 85, 87, 89, 88, 89, 91,
  ],
  97,
);
const NEAR_HILLS = ridge(
  [
    89, 87, 86, 87, 89, 91, 93, 95, 97, 98, 98, 98, 98, 98, 98, 98, 98, 98, 98, 98, 98, 98, 98, 98,
    98, 98, 98, 98, 98, 98, 98, 98, 98, 98, 95, 93, 92, 93, 94, 96,
  ],
  99,
);

/** 해 — 지평선(96)에 반쯤 걸친 픽셀 원 */
function sunPath(cx: number, cy: number, r: number) {
  const rows: string[] = [];
  for (let y = -r; y <= 0; y++) {
    const half = Math.round(Math.sqrt(r * r - y * y));
    rows.push(`M${cx - half} ${cy + y}h${half * 2}v1h-${half * 2}z`);
  }
  return rows.join('');
}

/** 땅 · 울타리 한 칸(48)을 반복합니다. 장면 폭(400) + 한 칸을 그려 두고 한 칸만큼 흘립니다 */
const TILE = 48;
const GROUND_W = 400 + TILE;

function groundPaths() {
  const tufts: string[] = [];
  const light: string[] = [];
  const spots: string[] = [];
  const shine: string[] = [];
  const posts: string[] = [];
  const postLight: string[] = [];
  for (let x = 0; x < GROUND_W; x += 16) {
    tufts.push(`M${x + 3} 115h1v3h-1zM${x + 4} 116h1v2h-1zM${x + 11} 116h1v2h-1z`);
    light.push(`M${x + 1} 118h6v1h-6zM${x + 9} 119h4v1h-4z`);
    spots.push(`M${x + 2} 130h2v2h-2zM${x + 10} 137h2v2h-2zM${x + 6} 144h2v2h-2z`);
    shine.push(`M${x + 12} 128h1v1h-1zM${x + 4} 140h1v1h-1z`);
  }
  for (let x = 8; x < GROUND_W; x += TILE) {
    posts.push(`M${x} 102h4v16h-4z`);
    postLight.push(`M${x} 102h1v16h-1z`);
  }
  return {
    tufts: tufts.join(''),
    light: light.join(''),
    spots: spots.join(''),
    shine: shine.join(''),
    posts: posts.join(''),
    postLight: postLight.join(''),
  };
}

const GROUND = groundPaths();

const SUN_X = 250;
/** 치치가 나는 자리 (스프라이트 왼쪽 위). 화면이 좁으면 양옆이 잘려서 가운데 가까이 둡니다 */
const CHICHI = { x: 170, y: 44 };

/** 하늘 위쪽 별. 화면 비율로 둡니다 */
const STARS = [
  { left: '8%', top: '6%' },
  { left: '22%', top: '14%' },
  { left: '37%', top: '4%' },
  { left: '58%', top: '11%' },
  { left: '71%', top: '5%' },
  { left: '86%', top: '16%' },
  { left: '93%', top: '7%' },
];

/** 움직임은 이 시안에서만 씁니다. 채택하면 index.css 로 옮깁니다 */
const CSS = `
@keyframes lp-ground { to { transform: translateX(-${TILE}px); } }
@keyframes lp-cloud { to { transform: translateX(-400px); } }
@keyframes lp-bob { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-4px); } }
@keyframes lp-flap { 0%, 49.9% { visibility: visible; } 50%, 100% { visibility: hidden; } }
@keyframes lp-wind { from { opacity: .9; transform: translateX(0); } to { opacity: 0; transform: translateX(-24px); } }
@keyframes lp-glint { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
@keyframes lp-twinkle { 0%, 100% { opacity: 1; } 50% { opacity: .2; } }
@keyframes lp-dot { 0%, 20% { opacity: 0; } 30%, 90% { opacity: 1; } 100% { opacity: 0; } }
.lp-ground { animation: lp-ground .8s steps(8) infinite; }
.lp-cloud { animation: lp-cloud 50s linear infinite; }
.lp-bob { animation: lp-bob 1.6s steps(4, jump-none) infinite; }
.lp-wing-up { animation: lp-flap .5s infinite; }
.lp-wing-down { animation: lp-flap .5s infinite reverse; }
.lp-wind { animation: lp-wind .7s steps(4) infinite; }
.lp-glint { animation: lp-glint 1.6s steps(2) infinite; }
.lp-twinkle { animation: lp-twinkle 2.4s steps(2) infinite; }
.lp-dot { animation: lp-dot 1.6s infinite; }
@media (prefers-reduced-motion: reduce) {
  .lp-ground, .lp-cloud, .lp-bob, .lp-wing-up, .lp-wind, .lp-glint, .lp-twinkle, .lp-dot { animation: none; }
  .lp-wing-down { display: none; }
}
`;

export function LoadingDraft() {
  const [variant, setVariant] = useState<Variant>('SCORING');
  const states = STATES[variant];
  const current = states.findIndex((s) => s === 'RUNNING' || s === 'WAITING');
  const waiting = states[current] === 'WAITING';

  return (
    <div
      className="relative flex min-h-dvh flex-col overflow-hidden"
      style={{ background: UPPER_SKY }}
    >
      <style>{CSS}</style>

      {STARS.map((s, i) => (
        <span
          key={i}
          aria-hidden="true"
          className="lp-twinkle absolute h-1 w-1"
          style={{ ...s, background: C.star, animationDelay: `${i * -0.35}s` }}
        />
      ))}

      <header className="relative z-10 flex flex-wrap items-center justify-between gap-3 px-5 pt-5 sm:px-8">
        <Link to="/" aria-label="홈으로" className="border-2 border-ink bg-panel px-3 py-2">
          <PitchCoachWordmark />
        </Link>
        <p className="tabular text-sm font-bold" style={{ color: C.text }}>
          캡스톤 최종 발표 / TAKE 4 · 발표 길이 04:38
        </p>
      </header>

      {/* 남은 하늘 가운데에 대화 상자. 장면과 겹치지 않아 날아가는 치치를 가리지 않습니다 */}
      <main className="relative z-10 flex flex-1 items-end justify-center px-5 pb-6 pt-4 sm:px-8">
        <Dialog waiting={waiting} states={states} current={current} />
      </main>

      <svg
        viewBox="0 0 400 150"
        preserveAspectRatio="xMidYMax slice"
        shapeRendering="crispEdges"
        role="img"
        aria-label="노을 진 바닷가 언덕 위를 치치가 날아가는 그림"
        className="block w-full shrink-0"
        style={{ height: 'max(40dvh, min(50dvh, 37.5vw))' }}
      >
        {SKY.map((b) => (
          <rect key={b.y} x="0" y={b.y} width="400" height={b.h} fill={b.fill} />
        ))}

        <g className="lp-cloud">
          {[0, 400].map((shift) => (
            <g key={shift}>
              <path d={cloudPath('1', shift)} fill={C.cloud} />
              <path d={cloudPath('2', shift)} fill={C.cloudShade} />
            </g>
          ))}
        </g>

        <path d={sunPath(SUN_X, 96, 17)} fill={C.sunRing} />
        <path d={sunPath(SUN_X, 96, 15)} fill={C.sun} />
        <path d={FAR_HILLS} fill={C.farHill} />
        <path d={NEAR_HILLS} fill={C.nearHill} />

        <rect x="0" y="96" width="400" height="22" fill={C.sea} />
        <rect x="0" y="107" width="400" height="11" fill={C.seaDeep} />
        <g className="lp-glint" fill={C.glint}>
          {[20, 14, 10, 6, 4].map((w, i) => (
            <rect key={w} x={SUN_X - w / 2} y={98 + i * 3} width={w} height="1" />
          ))}
        </g>

        <g className="lp-ground">
          <rect x="0" y="106" width={GROUND_W} height="2" fill={C.wood} />
          <rect x="0" y="112" width={GROUND_W} height="2" fill={C.wood} />
          <path d={GROUND.posts} fill={C.wood} />
          <path d={GROUND.postLight} fill={C.woodLight} />
          <rect x="0" y="118" width={GROUND_W} height="6" fill={C.grass} />
          <path d={GROUND.tufts} fill={C.grassDark} />
          <path d={GROUND.light} fill={C.grassLight} />
          <rect x="0" y="124" width={GROUND_W} height="26" fill={C.dirt} />
          <path d={GROUND.spots} fill={C.dirtDark} />
          <path d={GROUND.shine} fill={C.dirtLight} />
        </g>

        <g transform={`translate(${CHICHI.x} ${CHICHI.y})`}>
          <g className="lp-bob">
            {/* 꼬리 뒤로 흩어지는 바람 줄 */}
            <g fill={C.wind}>
              {[0, 1, 2].map((i) => (
                <rect
                  key={i}
                  className="lp-wind"
                  style={{ animationDelay: `${i * -0.23}s` }}
                  x={-6 - i * 4}
                  y={6 + i * 5}
                  width={6 - i}
                  height="1"
                />
              ))}
            </g>
            <g className="lp-wing-up">
              <Sprite rows={WING_UP} />
            </g>
            <g className="lp-wing-down">
              <Sprite rows={WING_DOWN} />
            </g>
          </g>
        </g>
      </svg>

      <DraftTools variant={variant} onVariant={setVariant} />
    </div>
  );
}

/**
 * 게임 대화 상자 — 진한 바탕에 두 겹 테두리. 팁은 치치가 말하는 대사처럼 이름표를 붙였습니다.
 * 말풍선을 치치에 붙이지 않은 이유: 치치가 움직이면 글이 같이 흔들리고, 좁은 화면에서 장면 밖으로 잘립니다.
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
      className="flex w-full max-w-xl flex-col items-center gap-4 border-4 px-5 py-5 text-center sm:px-8"
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
        보통 1분 안에 끝나요. 화면을 떠나도 분석은 계속돼요.
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
          <li>노을 색은 시안 안에만 있어요. 채택하면 index.css 의 @theme 에 둘지 정해야 해요.</li>
          <li>치치 날갯짓 두 장은 시안용 30×22 픽셀이에요. 원화에 맞춰 다시 그려야 해요.</li>
          <li>전체 화면이라 로그인 헤더(이름 · 로그아웃)를 이 화면에서 숨길지 정해야 해요.</li>
          <li>움직임 줄이기를 켠 사용자에게는 멈춘 장면을 보여 줘요.</li>
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
