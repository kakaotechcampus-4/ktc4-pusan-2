import { formatDuration } from '@/shared/lib/clock';
import type { ScriptMode, Slide } from '@/types/api';
import '../Stage/stage.css';

/**
 * 07 발표 연습 화면을 그대로 축소해 보여 줍니다.
 *
 * ★ 다시 그리지 않습니다. **무대와 같은 마크업**을 `.stage-preview` 안에 넣고
 *   CSS 로 축소만 합니다. 따로 그리면 대본 높이가 두 벌이 되고, 둘이 어긋나는
 *   순간 사용자가 보고 고른 화면과 실제로 서는 화면이 달라집니다.
 *   그 높이가 곧 시선 각도라 계측 조건이 달라집니다 (index.css 의 ★ 주석).
 *
 * 값은 전부 멈춰 있습니다 — 00:00, 첫 슬라이드, 코치 자리표시.
 * 여기서 확인하는 것은 숫자가 아니라 **모드를 바꿨을 때 화면이 어떻게 되는가**입니다.
 */
const MODE_NOTE: Record<ScriptMode, string> = {
  FULL: '전체 대본 모드 · 문장 전체 표시',
  HIGHLIGHT: '하이라이트 모드 · 현재 문장만 진하게',
  KEYWORD: '핵심 단어 모드 · 낱말만 표시',
  OFF: 'Off · 화면에 대본 없음',
};

export function StagePreview({
  mode,
  slides,
  paragraphs,
  timeLimitSec,
}: {
  mode: ScriptMode;
  slides: Slide[];
  paragraphs: string[];
  timeLimitSec: number;
}) {
  const total = slides.length;
  const first = slides[0];
  const keywords = (first?.keywords ?? []).map((k) => k.text);
  const limit = formatDuration(timeLimitSec * 1000);

  return (
    <div className="stage-preview h-115">
      {/* data-gaze 는 청중(초록)으로 고정합니다 — 세 색 중 하나는 보여야
          "테두리가 시선을 말한다"는 것이 전달됩니다 */}
      {/* 조작 대상이 아닙니다. 둘 다 답니다 —
          inert        : Tab 포커스·포인터·접근성 트리를 한 번에 막습니다
          aria-hidden  : inert 미지원 브라우저(Chrome 102 / Safari 15.5 이전)에서
                         이 장식용 무대가 스크린 리더에 읽히지 않게 합니다
          지금 이 안에 포커스 가능한 요소는 없어서 "aria-hidden 안의 포커스 대상"
          문제는 생기지 않습니다. 버튼이 생기면 inert 가 그쪽을 맡습니다 */}
      <div className="stage" data-script-mode={mode} data-gaze="AUDIENCE" inert="" aria-hidden>
        <header className="stage-head">
          <span className="elapsed">00:00</span>
          <span className="limit">/ {limit}</span>
          <div className="bar" data-over="false">
            <i style={{ width: '0%' }} />
          </div>
          <span className="meta">남은 {limit}</span>
          <span className="slide-no">SLIDE 1 / {total || '—'}</span>
        </header>

        <div className="viewport">
          <div className="slide">
            <span className="placeholder">SLIDE 1 · 16:9</span>
          </div>

          <div className="rail">
            <section className="camera">
              <div className="guide" />
              <span className="tag">CAMERA 16:9</span>
            </section>

            <section className="mic">
              <span className="text-xs text-stone">음량</span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-stage">
                <div className="h-full w-1/3 bg-coral" />
              </div>
              <span className="tabular text-xs">—dB</span>
            </section>

            <section className="next">
              <div className="thumb">SLIDE 2 · 16:9</div>
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

        <div className="coach">
          <span>실시간 코칭 문구가 이 자리에 표시됩니다</span>
          <span className="rule">한 번에 하나만 · 8초 후 사라짐</span>
        </div>

        {mode !== 'OFF' && (
          <div className="script">
            {mode === 'KEYWORD' ? (
              <div className="keywords">
                {keywords.length === 0 ? (
                  <span>이 슬라이드에는 정해 둔 낱말이 없어요</span>
                ) : (
                  keywords.map((k) => <span key={k}>{k}</span>)
                )}
              </div>
            ) : (
              paragraphs.slice(0, 4).map((text, i) => (
                <p key={i} data-current={i === 0}>
                  {text}
                </p>
              ))
            )}

            <div className="foot">
              <span>{MODE_NOTE[mode]}</span>
              <span>↑↓ 대본 · ←→ 슬라이드</span>
            </div>
          </div>
        )}

        <div className="stage-foot">
          <span className="rec">기록 중 · 0.0MB</span>
          <span>시선 기록 중</span>
        </div>
      </div>
    </div>
  );
}
