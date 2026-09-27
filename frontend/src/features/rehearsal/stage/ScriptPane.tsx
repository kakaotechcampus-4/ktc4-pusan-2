import { useEffect, useRef } from 'react';
import type { ScriptMode } from '@/types/api';

/** ↑/↓ 한 번에 움직이는 양. 한 문장 정도입니다 */
const SCROLL_STEP = 56;

/** 맞춰 올린 문단 위에 남기는 숨 쉴 틈 */
const SCROLL_MARGIN = 8;

/**
 * 발표 중 대본.
 *
 * 높이는 여기서 정하지 않습니다 — `.stage`의 `--script-h`가 정합니다.
 * 그 값이 곧 시선 각도이고 Calibration 기준이라, 컴포넌트가 제 높이를 주장하면
 * 계측 조건이 마크업으로 흩어집니다 (index.css의 ★ 주석).
 *
 * ── 지금 문단을 어떻게 아는가 ────────────────────────────────────────
 * 슬라이드 앵커(`script.slideAnchors`)를 씁니다. 발화 위치가 아니라 슬라이드 기준입니다.
 * 말한 내용으로 문장을 따라가려면 STT가 필요하고 그건 2단(서버)입니다 —
 * 끊기면 대본이 엉뚱한 곳에 멈추므로, 끊겨도 맞는 기준을 씁니다.
 */
export function ScriptPane({
  mode,
  paragraphs,
  currentIndex,
  keywords,
}: {
  mode: ScriptMode;
  paragraphs: string[];
  /** 지금 슬라이드에 해당하는 문단 번호. 없으면 -1 */
  currentIndex: number;
  keywords: string[];
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const currentRef = useRef<HTMLParagraphElement>(null);

  // 슬라이드를 넘기면 그 문단이 위로 오게 맞춥니다. scrollIntoView 대신
  // 컨테이너 scrollTop 을 직접 씁니다 — scrollIntoView 는 페이지 전체를 움직입니다.
  //
  // ★ offsetTop 을 쓰지 않습니다. offsetTop 은 offsetParent 기준인데
  //   stage.css 의 `.script` 에 position: relative 가 걸려 있어 그 기준이 이미 이 상자입니다.
  //   거기서 box.offsetTop(무대 안에서 상자가 앉은 위치)을 또 빼면 값이 음수가 되고,
  //   Math.max 가 0 으로 눌러 **어느 문단이든 맨 위로 튀었습니다.**
  //
  //   두 사각형의 지금 화면상 거리로 구하면 상자의 position·padding 이 무엇이든 맞습니다.
  //   stage.css 는 계측 조건이라 앞으로도 바뀔 수 있는데, 그때 여기가 같이 깨지면 안 됩니다.
  useEffect(() => {
    const box = boxRef.current;
    const p = currentRef.current;
    if (!box || !p) return;
    const delta = p.getBoundingClientRect().top - box.getBoundingClientRect().top;
    box.scrollTop = Math.max(0, box.scrollTop + delta - SCROLL_MARGIN);
  }, [currentIndex, mode]);

  // ↑/↓ 는 대본만 움직입니다. ←/→ 는 슬라이드라 useSlideDeck이 받습니다
  useEffect(() => {
    if (mode === 'OFF') return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      const box = boxRef.current;
      if (!box) return;
      e.preventDefault();
      box.scrollTop += e.key === 'ArrowDown' ? SCROLL_STEP : -SCROLL_STEP;
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mode]);

  if (mode === 'OFF') return null;

  return (
    <div className="script" ref={boxRef}>
      {mode === 'KEYWORD' ? (
        <div className="keywords">
          {keywords.length === 0 ? (
            <span>이 슬라이드에는 정해 둔 낱말이 없어요</span>
          ) : (
            keywords.map((k) => <span key={k}>{k}</span>)
          )}
        </div>
      ) : (
        paragraphs.map((text, i) => (
          <p
            key={i}
            data-current={i === currentIndex}
            ref={i === currentIndex ? currentRef : undefined}
          >
            {text}
          </p>
        ))
      )}

      <div className="foot">
        <span>↑↓ 대본 · ←→ 슬라이드 · 자동 스크롤</span>
      </div>
    </div>
  );
}
