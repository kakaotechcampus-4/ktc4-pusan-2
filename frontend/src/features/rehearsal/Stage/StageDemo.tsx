import { useEffect, useRef, useState } from 'react';
import type { GazeZone, ScriptMode } from '@/types/api';
import './stage.css';

/** contract의 Zone → 테두리 상태. 워커가 뭘 주든 매핑은 한 곳에서만 한다. */
const BORDER: Record<GazeZone, string> = {
  CAMERA: 'AUDIENCE',
  BOTTOM: 'SCREEN',
  UNCERTAIN: 'UNKNOWN',
};

/**
 * /dev/stage — 무대 레이아웃 검증용. 제품 화면이 아니다.
 *
 * 이 페이지가 존재하는 이유
 * ────────────────────────
 * 1. Script Mode 네 벌의 높이가 실제로 맞는지 눈으로 본다
 * 2. 시선 테두리가 **리렌더링 없이** 바뀌는지 확인한다
 * 3. Tailwind와 stage.css가 한 화면에서 충돌 없이 도는지 본다
 *
 * 시선 버튼을 눌러도 아래 렌더 카운터가 안 올라가야 정상이다.
 * 올라가면 어딘가에서 setState를 하고 있다는 뜻이고,
 * 그 상태로 초당 15번 판정이 들어오면 타이머가 밀린다.
 */
export function StageDemo() {
  const stageRef = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<ScriptMode>('FULL');

  // 렌더 횟수를 세는 것도 DOM에 직접 쓴다 — 세느라 렌더를 유발하면 의미가 없다
  const countRef = useRef(0);
  const badgeRef = useRef<HTMLElement>(null);
  useEffect(() => {
    countRef.current += 1;
    if (badgeRef.current) badgeRef.current.textContent = String(countRef.current);
  });

  // ★ 핵심 — 시선은 React 상태를 거치지 않는다
  const setGaze = (zone: GazeZone) => {
    if (stageRef.current) stageRef.current.dataset.gaze = BORDER[zone];
  };

  // 말풍선도 같은 이유로 DOM에 직접 쓴다. 실제 코치 메시지는 Zustand로 오지만,
  // 여기서 확인하려는 건 "대본 영역 바로 위에 앉는가" 하나다.
  const coachRef = useRef<HTMLDivElement>(null);
  const toggleCoach = () => {
    if (coachRef.current) coachRef.current.hidden = !coachRef.current.hidden;
  };

  return (
    <div className="h-full flex flex-col bg-greige">
      {/* 조작부는 평범한 UI라 Tailwind로 */}
      <div className="flex flex-wrap items-center gap-2 p-3 border-b border-line bg-panel">
        <span className="text-xs font-bold text-stone mr-1">시선</span>
        {(['CAMERA', 'BOTTOM', 'UNCERTAIN'] as GazeZone[]).map((z) => (
          <button
            key={z}
            onClick={() => setGaze(z)}
            className="px-3 py-1.5 rounded-full text-xs font-semibold border border-line
                       bg-cream hover:bg-coral-wash"
          >
            {z}
          </button>
        ))}

        <span className="text-xs font-bold text-stone ml-4 mr-1">대본</span>
        {(['FULL', 'HIGHLIGHT', 'KEYWORD', 'OFF'] as ScriptMode[]).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`px-3 py-1.5 rounded-full text-xs font-semibold border ${
              mode === m
                ? 'bg-coral text-white border-coral'
                : 'bg-cream border-line hover:bg-coral-wash'
            }`}
          >
            {m}
          </button>
        ))}

        <button
          onClick={toggleCoach}
          className="ml-4 rounded-full border border-line bg-cream px-3 py-1.5 text-xs
                     font-semibold hover:bg-coral-wash"
        >
          말풍선
        </button>

        <span className="ml-auto text-xs text-stone tabular">
          렌더{' '}
          <b className="text-ink" ref={badgeRef}>
            0
          </b>{' '}
          — 시선 버튼으로는 안 올라야 정상
        </span>
      </div>

      {/* 무대는 전용 CSS로 */}
      <div className="flex-1 min-h-0 relative">
        <div ref={stageRef} className="stage" data-script-mode={mode} data-gaze="UNKNOWN">
          <div className="slide">
            <span className="text-stone text-sm">슬라이드 영역 — 남는 만큼 커집니다</span>
          </div>
          {/* ★ .stage 의 자식이어야 한다 — stage.css 의 .stage > .coach 주석 참고.
              Script Mode 를 바꿀 때 말풍선이 대본 높이를 따라 올라가야 정상이다. */}
          <div ref={coachRef} className="coach" hidden>
            조금 빨라요. 한 호흡 쉬고 가세요
          </div>
          <div className="script">
            안녕하세요. 오늘 저희가 소개할 서비스는 발표 연습을 도와주는 <mark>피치코치</mark>
            입니다. 발표를 앞두고 혼자 연습할 때 가장 어려운 건, 내가 지금 잘하고 있는지를 스스로 알
            수 없다는 점입니다. 말이 빨라졌는지, 청중을 보고 있는지, 대본에 얼마나 기대고 있는지는
            발표하는 본인이 가장 모릅니다.
          </div>
        </div>
      </div>
    </div>
  );
}
