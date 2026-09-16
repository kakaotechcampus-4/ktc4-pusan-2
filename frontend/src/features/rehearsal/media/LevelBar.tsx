import type { RefObject } from 'react';

/** 칸 수. 칸이 있으면 "지금 어느 정도인지"를 숫자 없이도 읽을 수 있습니다 */
const SEGMENTS = 14;

/**
 * 마이크 음량계.
 *
 * 값은 React를 거치지 않습니다 — `useMicLevel`이 rAF 안에서 이 엘리먼트에 직접 씁니다.
 * 어떻게 칠할지는 `data-meter`가 정합니다 (clip · width).
 */
export function LevelBar({
  variant,
  meterRef,
  dbRef,
}: {
  variant: 'segments' | 'bar';
  meterRef: RefObject<HTMLDivElement>;
  dbRef: RefObject<HTMLSpanElement>;
}) {
  return (
    <div className="flex items-center gap-3">
      <span className="shrink-0 text-xs text-stone">음량</span>

      {variant === 'segments' ? (
        <div className="relative h-4 flex-1">
          <div className="absolute inset-0 flex gap-1">
            {Array.from({ length: SEGMENTS }, (_, i) => (
              <span key={i} className="h-full flex-1 rounded-xs bg-stage-panel" />
            ))}
          </div>
          {/* 칸 수를 유지해야 해서 폭이 아니라 잘라냅니다 */}
          <div
            ref={meterRef}
            data-meter="clip"
            className="absolute inset-0 flex gap-1"
            style={{ clipPath: 'inset(0 100% 0 0)' }}
          >
            {Array.from({ length: SEGMENTS }, (_, i) => (
              <span key={i} className="h-full flex-1 rounded-xs bg-coral" />
            ))}
          </div>
        </div>
      ) : (
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-stage-panel">
          <div ref={meterRef} data-meter="width" className="h-full bg-coral" style={{ width: 0 }} />
        </div>
      )}

      <span className="tabular shrink-0 text-sm">
        <span ref={dbRef}>—</span>
        <span className="ml-1 text-xs text-stone">dB</span>
      </span>
    </div>
  );
}
