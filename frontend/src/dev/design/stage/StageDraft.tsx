import { useState, type CSSProperties } from 'react';
import { Button } from '@/shared/ui/Button';
import { DraftPage } from '../ui';

/**
 * 리허설 무대 스타일 시안 — 실제 무대는 `features/rehearsal/stage/stage.css`, 배치 검증은 `/dev/stage`.
 *
 * **배치는 그대로, 겉모양만** 다른 시안(진한 테두리 · 각진 모서리 · 밀린 그림자)에 맞춥니다.
 * 아래 값은 stage.css 와 같아야 합니다 — 디자인이 아니라 계측 조건이라 시안에서도 바꾸지 않습니다.
 *   줄 순서       머리 · 무대 · 코치(46px, 비어도 자리 유지) · 대본(모드별 고정) · 발밑
 *   레일 폭       320px
 *   대본 높이     --spacing-script-highlight · --spacing-script-keyword · 0
 *   시선 테두리   3색 + 두께 차이를 padding으로 상쇄 (안쪽 상자가 움직이지 않게)
 */

type Surface = 'LIGHT' | 'DARK';
type Gaze = 'AUDIENCE' | 'SCREEN' | 'UNKNOWN';
type ScriptMode = 'HIGHLIGHT' | 'KEYWORD' | 'OFF';

const VARIANTS = [
  { id: 'LIGHT' as const, label: '밝은 무대' },
  { id: 'DARK' as const, label: '어두운 무대' },
];

/**
 * 지면별 색. 어두운 무대는 무대 토큰(`bg-stage` 등)을 쓰고, 값은 아래 `BLACK_STAGE`로 이 시안에서만 바꿉니다.
 * 어두운 지면에서는 진한 테두리가 묻혀서 테두리도 밝은 선으로 바꿉니다.
 */
const SURFACE: Record<
  Surface,
  { stage: string; panel: string; deep: string; text: string; muted: string; line: string }
> = {
  LIGHT: {
    line: 'border-ink',
    stage: 'bg-greige text-ink',
    panel: 'bg-panel',
    deep: 'bg-cream',
    text: 'text-ink',
    muted: 'text-stone',
  },
  DARK: {
    line: 'border-ink-stage/35',
    stage: 'bg-stage text-ink-stage',
    panel: 'bg-stage-panel',
    deep: 'bg-stage-deep',
    text: 'text-ink-stage',
    muted: 'text-ink-stage/60',
  },
};

/**
 * 더 검은 무대 후보값 — **시안 전용**입니다. `pitch-create` 처럼 같은 토큰 이름을 이 상자 안에서만 바꿉니다.
 * 채택하면 `index.css` @theme 의 무대 토큰 세 개를 이 값으로 바꾸면 됩니다 (실제 무대 전체에 적용).
 *   지금  stage #332E28 · stage-panel #443D35 · stage-deep #2A2622
 */
const BLACK_STAGE = {
  '--color-stage': '#161514',
  '--color-stage-panel': '#22201E',
  '--color-stage-deep': '#0C0B0A',
} as CSSProperties;

/** stage.css 의 `.stage[data-gaze=…] .viewport` 그대로. 두께가 줄면 padding 으로 채웁니다 */
const GAZE_BORDER: Record<Gaze, CSSProperties> = {
  AUDIENCE: { borderColor: 'var(--color-gaze-audience)', borderWidth: 2, padding: 3 },
  SCREEN: { borderColor: 'var(--color-gaze-screen)', borderWidth: 5, padding: 0 },
  UNKNOWN: {
    borderColor: 'var(--color-gaze-unknown)',
    borderWidth: 5,
    padding: 0,
    borderStyle: 'dashed',
  },
};

const SCRIPT_H: Record<ScriptMode, string> = {
  HIGHLIGHT: 'var(--spacing-script-highlight)',
  KEYWORD: 'var(--spacing-script-keyword)',
  OFF: 'var(--spacing-script-off)',
};

const SCRIPT = [
  '안녕하세요. 오늘 저희가 소개할 서비스는 발표 연습을 도와주는 피치코치입니다.',
  '발표를 앞두고 혼자 연습할 때 가장 어려운 건, 내가 지금 잘하고 있는지를 스스로 알 수 없다는 점입니다.',
  '말이 빨라졌는지, 청중을 보고 있는지, 대본에 얼마나 기대고 있는지는 발표하는 본인이 가장 모릅니다.',
];

const KEYWORDS = ['피치코치', '혼자 연습의 한계', '속도 · 시선 · 대본 의존'];

export function StageDraft() {
  const [surface, setSurface] = useState<Surface>('LIGHT');
  const [gaze, setGaze] = useState<Gaze>('AUDIENCE');
  const [scriptMode, setScriptMode] = useState<ScriptMode>('HIGHLIGHT');
  const [coach, setCoach] = useState(true);
  const s = SURFACE[surface];
  const box = `border-2 ${s.line} ${s.panel}`;

  return (
    <DraftPage
      title="리허설 무대"
      eyebrow="캡스톤 중간발표 / TAKE 5"
      meta="스타일 시안 · 배치는 실제 무대와 같음"
      variants={VARIANTS}
      variant={surface}
      onVariant={setSurface}
      questions={[
        '밝은 무대로 바꿀지 정해야 해요. 지금 무대가 어두운 건 화면 반사를 줄이고 슬라이드에 집중시키려는 이유예요. 밝게 하면 카메라 미리보기만 어둡게 둘 수 있어요 (시작 전 세팅과 같은 방식).',
        '시선 테두리 3색은 무대 전용이라 그대로 뒀어요. 밝은 지면에서도 셋이 잘 구분되는지 봐 주세요.',
        '대본 높이 · 레일 폭 · 코치 줄 높이는 계측 조건이라 바꾸지 않았어요. 바꾸려면 팀 합의가 필요해요 (frontend/CLAUDE.md 7번).',
      ]}
    >
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border border-dashed border-line-strong px-4 py-2.5 text-sm">
        <Toggle
          label="시선"
          value={gaze}
          options={['AUDIENCE', 'SCREEN', 'UNKNOWN']}
          onChange={setGaze}
        />
        <Toggle
          label="대본"
          value={scriptMode}
          options={['HIGHLIGHT', 'KEYWORD', 'OFF']}
          onChange={setScriptMode}
        />
        <button
          type="button"
          aria-pressed={coach}
          onClick={() => setCoach((v) => !v)}
          className={`px-3 py-1 font-bold ${coach ? 'bg-ink text-panel' : 'bg-panel text-stone'}`}
        >
          코치 줄
        </button>
      </div>

      <div
        className={`grid h-[760px] gap-3 border-2 border-ink p-4 shadow-[4px_4px_0_var(--color-line)] ${s.stage}`}
        style={{
          gridTemplateRows: `auto 1fr 46px ${SCRIPT_H[scriptMode]} auto`,
          ...(surface === 'DARK' ? BLACK_STAGE : {}),
        }}
      >
        {/* 머리 */}
        <div className="flex items-center gap-4">
          <p className="tabular font-mono text-3xl font-bold">
            06:32 <span className={`text-base font-normal ${s.muted}`}>/ 10:00</span>
          </p>
          <div className={`h-3 flex-1 border-2 ${s.line} ${s.deep}`}>
            <div className="h-full w-[65%] bg-coral" />
          </div>
          <p className={`tabular font-mono text-sm ${s.muted}`}>
            남은 03:28 <span className={`ml-3 font-bold ${s.text}`}>SLIDE 6 / 12</span>
          </p>
        </div>

        {/* 무대 = 슬라이드 + 레일. 시선 테두리가 붙는 곳 */}
        <div
          className="grid min-h-0 grid-cols-[1fr_320px] gap-3 border-solid transition-colors duration-[400ms]"
          style={GAZE_BORDER[gaze]}
        >
          <div className={`flex items-center justify-center ${box} ${s.deep}`}>
            <p className={`font-mono text-sm ${s.muted}`}>SLIDE 6 · 16:9</p>
          </div>
          <div className="flex min-h-0 flex-col gap-3">
            {/*
              카메라 — 찌그러뜨리지 않고 잘라서 채웁니다 (object-cover, 실제 무대의 `.rail .camera video` 와 같음).
              비율은 16:9 고정이고, 영상이 다른 비율이면 가운데를 기준으로 잘라 맞춥니다. 거울상으로 보여 줍니다.
            */}
            <div className={`relative overflow-hidden ${box} ${s.deep} aspect-video`}>
              <img
                src="/onboarding/chichi-portrait-longsleeve.png"
                alt=""
                className="absolute inset-0 h-full w-full -scale-x-100 object-cover"
              />
              <span className="absolute left-2 top-2 bg-ink px-2 py-0.5 text-xs font-bold text-panel">
                ● 시선 · 카메라
              </span>
              <span className="absolute bottom-2 left-2 bg-ink/70 px-1.5 font-mono text-[10px] text-panel">
                예시 영상 · 16:9
              </span>
            </div>
            <div className={`flex items-center gap-3 px-3 py-2.5 ${box}`}>
              <span className={`font-mono text-xs ${s.muted}`}>MIC</span>
              <span className="flex flex-1 gap-1" aria-hidden="true">
                {[1, 1, 1, 1, 1, 0, 0, 0].map((on, i) => (
                  <i key={i} className={`h-2.5 flex-1 ${on ? 'bg-coral' : s.deep}`} />
                ))}
              </span>
              <span className="tabular font-mono text-xs">-11 dB</span>
            </div>
            <div className={`flex gap-3 p-3 ${box}`}>
              <div className={`aspect-video w-28 shrink-0 border ${s.line} ${s.deep}`} />
              <div>
                <p className={`font-mono text-xs ${s.muted}`}>NEXT</p>
                <p className="text-sm font-bold">연습이 변화가 되는 순간</p>
                <p className={`text-xs ${s.muted}`}>→ 키로 이동</p>
              </div>
            </div>
          </div>
        </div>

        {/* 코치 줄 — 메시지가 없어도 자리는 남습니다 */}
        <div
          className={`flex items-center gap-3 px-3 ${coach ? 'border-2 border-ink bg-coral-wash text-ink' : ''}`}
        >
          {coach && (
            <>
              <img
                src="/onboarding/chichi-mascot.png"
                alt=""
                width={32}
                height={38}
                className="h-9 w-8 object-contain [image-rendering:pixelated]"
              />
              <p className="flex-1 text-sm">
                <b className="text-coral-deep">조금만 천천히 말해 볼까요?</b>
                <span className="ml-2 text-stone">지금 352자/분</span>
              </p>
              <span className="text-xs text-stone">잠시 후 사라져요</span>
            </>
          )}
        </div>

        {/* 대본 — 높이는 모드별 고정 (계측 조건) */}
        <div
          className={`min-h-0 overflow-hidden ${scriptMode === 'OFF' ? '' : `${box} px-4 py-3`}`}
        >
          {scriptMode === 'HIGHLIGHT' && (
            <div className="space-y-2 text-sm leading-relaxed">
              {SCRIPT.map((line, i) => (
                <p key={line} className={i === 0 ? 'font-bold' : s.muted}>
                  {line}
                </p>
              ))}
            </div>
          )}
          {scriptMode === 'KEYWORD' && (
            <ul className="flex flex-wrap gap-2">
              {KEYWORDS.map((k, i) => (
                <li
                  key={k}
                  className={`border-2 ${s.line} px-3 py-1.5 text-sm font-bold ${i === 0 ? 'bg-coral-wash text-ink' : ''}`}
                >
                  {k}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* 발밑 */}
        <div className="flex items-center justify-between gap-3">
          <p className={`text-xs ${s.muted}`}>● 시선 기록 중</p>
          <p className={`font-mono text-xs ${s.muted}`}>↑↓ 대본 · ←→ 슬라이드</p>
          <Button>■ 발표 끝내기</Button>
        </div>
      </div>
    </DraftPage>
  );
}

function Toggle<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: T[];
  onChange: (v: T) => void;
}) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="mr-1 text-xs font-bold text-stone">{label}</span>
      {options.map((o) => (
        <button
          key={o}
          type="button"
          aria-pressed={value === o}
          onClick={() => onChange(o)}
          className={`px-2.5 py-1 font-bold ${value === o ? 'bg-ink text-panel' : 'bg-panel text-stone hover:text-ink'}`}
        >
          {o}
        </button>
      ))}
    </span>
  );
}
