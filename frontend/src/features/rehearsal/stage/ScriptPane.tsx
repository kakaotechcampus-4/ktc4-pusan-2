import { useEffect, useMemo, useRef } from 'react';
import type { ScriptMode } from '@/types/api';
import { markScriptLines, type TextRange } from '../lib/scriptMarks';

/** 하이라이트가 아닌 모드에서 넘기는 빈 값. 렌더마다 새 배열이면 강조 계산이 매번 다시 돕니다 */
const NONE: never[] = [];

/** ↑/↓ 한 번에 움직이는 양. 한 문장 정도입니다 */
const SCROLL_STEP = 56;

/**
 * 발표 중 대본 — **지금 슬라이드에 매핑된 대본만** 보여 줍니다.
 *
 * 높이는 여기서 정하지 않습니다 — `.stage`의 `--script-h`가 정합니다.
 * 그 값이 곧 시선 각도이고 Calibration 기준이라, 컴포넌트가 제 높이를 주장하면
 * 계측 조건이 마크업으로 흩어집니다 (index.css의 ★ 주석).
 *
 * ── 왜 전체가 아니라 지금 장만인가 ──────────────────────────────────
 * 대본은 피치 생성의 대본 매핑에서 슬라이드별로 나뉘어 있습니다. 슬라이드 1이 떠 있으면
 * 매핑 1의 글만 보입니다. 전체를 띄워 두면 다른 장의 글까지 눈에 들어와 읽을 자리를 찾느라
 * 시선이 대본에 더 오래 머뭅니다.
 *
 * 기준은 발화 위치가 아니라 슬라이드입니다. 말한 내용으로 따라가려면 STT가 필요하고
 * 그건 2단(서버)입니다 — 끊기면 대본이 엉뚱한 곳에 멈추므로, 끊겨도 맞는 기준을 씁니다.
 */
export function ScriptPane({
  mode,
  slideNumber,
  text,
  keywords,
  highlights,
}: {
  mode: ScriptMode;
  /** 지금 슬라이드. 바뀌면 대본을 맨 위로 되돌립니다 */
  slideNumber: number;
  /** 지금 슬라이드에 매핑된 대본. 매핑이 없으면 빈 문자열 */
  text: string;
  /** AI 가 뽑은 낱말. 핵심 키워드 모드는 칩으로, 하이라이트 모드는 대본 속 강조로 보입니다 */
  keywords: string[];
  /** 그 낱말의 대본 속 자리. 비어 있으면 낱말이 처음 나오는 자리를 찾아 강조합니다 */
  highlights: TextRange[];
}) {
  const boxRef = useRef<HTMLDivElement>(null);

  // 슬라이드를 넘기면 새 장의 대본을 처음부터 읽습니다. 앞 장에서 내려 둔 스크롤을 되돌립니다
  useEffect(() => {
    if (boxRef.current) boxRef.current.scrollTop = 0;
  }, [slideNumber, mode]);

  // 줄바꿈마다 한 문단. 매핑 안에서 사용자가 나눠 둔 줄을 그대로 살립니다.
  // 강조는 하이라이트 모드에서만 — 다른 모드에서 색이 붙으면 지금 문단 표시와 섞입니다
  const highlight = mode === 'HIGHLIGHT';
  const lines = useMemo(
    () => markScriptLines(text, highlight ? highlights : NONE, highlight ? keywords : NONE),
    [text, highlight, highlights, keywords],
  );

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
        <>
          {lines.length === 0 ? (
            <p>이 슬라이드에는 매핑된 대본이 없어요</p>
          ) : (
            lines.map((parts, i) => (
              <p key={`${slideNumber}-${i}`} data-current>
                {parts.map((part, j) => (part.mark ? <mark key={j}>{part.text}</mark> : part.text))}
              </p>
            ))
          )}
        </>
      )}

      <div className="foot">
        <span>↑↓ 대본 · ←→ 슬라이드 · 지금 슬라이드의 대본</span>
      </div>
    </div>
  );
}
