import type { RefObject } from 'react';
import './audio-input-meter.css';

/** useMicLevel이 DOM에 입력값을 직접 그린다. 별도 권한 요청이나 오디오 계량기는 만들지 않는다. */
export function AudioInputMeter({ meterRef }: { meterRef: RefObject<HTMLDivElement> }) {
  return (
    <div
      ref={meterRef}
      className="prepare-input-meter flex h-10 w-full items-center gap-3 border border-meter-line bg-meter-panel px-2.5 py-2 text-meter-ink"
      data-meter="input"
      role="meter"
      aria-label="마이크 입력 레벨"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={0}
    >
      <svg
        className="shrink-0"
        width="20"
        height="20"
        viewBox="0 0 16 16"
        fill="currentColor"
        shapeRendering="crispEdges"
        aria-hidden="true"
      >
        <path d="M6 1h4v1h1v7h-1v1H6V9H5V2h1ZM2 6h2v4h1v1h1v1h4v-1h1v-1h1V6h2v4h-1v2h-2v1H9v2h3v1H4v-1h3v-2H5v-1H3v-2H2Z" />
      </svg>
      <div className="prepare-input-track" aria-hidden="true">
        {Array.from({ length: 48 }, (_, index) => (
          <span key={index} />
        ))}
        <div className="prepare-input-fill">
          {Array.from({ length: 48 }, (_, index) => (
            <span key={index} data-band={band(index)} />
          ))}
        </div>
      </div>
    </div>
  );
}

function band(index: number) {
  if (index < 32) return 'green';
  if (index < 42) return 'yellow';
  return 'red';
}
