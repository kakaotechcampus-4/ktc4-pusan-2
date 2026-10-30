import { useEffect, useRef, useState } from 'react';
import type { GazeZone, ScriptMode } from '@/types/api';
import { ScriptPane } from '@/dev/stage/ScriptPane';
import { CoachStrip } from '@/dev/stage/CoachStrip';
import './stage.css';
import '@/dev/stage/stage-preview.css';

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
 * 1. 대본 세 모드의 시안 높이와 위치를 눈으로 본다
 * 2. 시선 테두리가 **리렌더링 없이** 바뀌는지 확인한다
 * 3. Tailwind와 stage.css가 한 화면에서 충돌 없이 도는지 본다
 *
 * 시선 버튼을 눌러도 아래 렌더 카운터가 안 올라가야 정상이다.
 * 올라가면 어딘가에서 setState를 하고 있다는 뜻이고,
 * 그 상태로 초당 15번 판정이 들어오면 타이머가 밀린다.
 *
 * ★ PM 승인 전 배치 제안이다. 제품의 무대 배치·측정 계약은 변경하지 않는다.
 *   시안의 행은 머리·무대·코치·조작 안내·대본 순서다.
 *   종료는 상단, 조작 안내는 코치 아래, 대본은 맨 아래에 둔다.
 */
export function StageDemo() {
  const stageRef = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<ScriptMode>('HIGHLIGHT');
  const [tools, setTools] = useState(
    () => new URLSearchParams(window.location.search).get('tools') !== 'hide',
  );
  const [showCoach, setShowCoach] = useState(false);

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

  // 코치 줄도 같은 이유로 DOM에 직접 쓴다. 여기서 확인하려는 건
  // "떴다 사라져도 위 칸이 안 움직이는가" 하나다 — 자리를 늘 잡아 두기 때문이다.
  const toggleCoach = () => {
    setShowCoach((shown) => !shown);
  };

  return (
    <div className="stage-design-preview flex h-full flex-col bg-greige">
      {/* 조작부는 평범한 UI라 Tailwind로 */}
      {tools && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line bg-panel p-3">
          <button
            type="button"
            onClick={() => setTools(false)}
            className="border border-line px-2 py-1 text-xs"
          >
            검토 도구 숨기기
          </button>
          <span className="mr-1 text-xs font-bold text-stone">시선</span>
          {(['CAMERA', 'BOTTOM', 'UNCERTAIN'] as GazeZone[]).map((z) => (
            <button
              key={z}
              onClick={() => setGaze(z)}
              className="rounded-full border border-line bg-cream px-3 py-1.5 text-xs font-semibold
                       hover:bg-coral-wash"
            >
              {z}
            </button>
          ))}

          <span className="mr-1 ml-4 text-xs font-bold text-stone">대본</span>
          {(['HIGHLIGHT', 'KEYWORD', 'OFF'] as ScriptMode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${
                mode === m
                  ? 'border-coral bg-coral text-white'
                  : 'border-line bg-cream hover:bg-coral-wash'
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
            코치 줄
          </button>

          <span className="tabular ml-auto text-xs text-stone">
            렌더{' '}
            <b className="text-ink" ref={badgeRef}>
              0
            </b>{' '}
            — 시선 변경 시 유지
          </span>
        </div>
      )}

      {/* 무대는 전용 CSS로 */}
      <div className="relative min-h-0 flex-1">
        <div
          ref={stageRef}
          className="stage stage-live"
          data-script-mode={mode}
          data-gaze="UNKNOWN"
        >
          <header className="stage-head">
            <span className="elapsed">06:32</span>
            <span className="limit">/ 10:00</span>
            <div className="bar" data-over="false">
              <i style={{ width: '65%' }} />
            </div>
            <span className="meta">남은 03:28</span>
            <span className="slide-no">SLIDE 6 / 12</span>
            <button className="end" type="button" disabled aria-describedby="end-hint">
              발표 끝내기
            </button>
          </header>

          {/* ★ 시선 테두리는 이 상자에 붙는다 — .stage 의 data-gaze 가 받는다 */}
          <div className="viewport">
            <div className="slide">
              <div className="slide-content">
                <div className="stage-demo-slide">
                  <h2>
                    혼자 하는 연습에서
                    <br />
                    <strong>변화를 확인하는 연습으로</strong>
                  </h2>
                  <p>피치코치</p>
                  <span>발표 → 피드백 → 다음 Take</span>
                </div>
              </div>
            </div>

            <div className="rail">
              <section className="camera">
                <div className="frame">
                  <img
                    className="stage-demo-camera"
                    src="/onboarding/chichi-portrait-longsleeve.png"
                    alt="카메라 예시 이미지"
                  />
                  <span className="tag">예시 이미지 · 4:3</span>
                </div>
              </section>
              <section className="mic">
                <span className="text-xs text-stone">음량 — 실제 값은 P5에서</span>
              </section>
              <section className="next">
                <div className="thumb">SLIDE 7 · 16:9</div>
                <div className="label">
                  <b>다음 슬라이드</b>
                  <span>→ 키로 이동</span>
                </div>
              </section>
              <div className="legend">
                <span>테두리 =</span>
                <i className="audience">청중</i>
                <i className="screen">화면</i>
                <i className="unknown">판정 불가</i>
              </div>
            </div>
          </div>

          <CoachStrip
            message={showCoach ? '조금 빨라요. 한 호흡 쉬고 가세요' : null}
            exam={mode === 'OFF'}
          />

          <ScriptPane
            mode={mode}
            slideNumber={6}
            text={
              '안녕하세요. 오늘 저희가 소개할 서비스는 발표 연습을 도와주는 피치코치입니다.\n발표를 앞두고 혼자 연습할 때 가장 어려운 건, 내가 지금 잘하고 있는지를 스스로 알 수 없다는 점입니다.\n말이 빨라졌는지, 청중을 보고 있는지, 대본에 얼마나 기대고 있는지는 발표하는 본인이 가장 모릅니다.'
            }
            keywords={['피치코치', '혼자 연습의 한계', '속도 · 시선 · 대본 의존']}
            highlights={[]}
          />

          <div className="stage-foot">
            <div className="stage-notes">
              <span className="stage-status" id="end-hint">
                마지막 장에서 끝낼 수 있어요
              </span>
              <span className="key-help">
                ← → 슬라이드 이동 · Space 다음 장{mode !== 'OFF' && ' · ↑ ↓ 대본 스크롤'}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
