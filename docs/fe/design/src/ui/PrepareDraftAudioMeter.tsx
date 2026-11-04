/** 독립 시안의 입력 레벨 표시. 실제 마이크를 열거나 사용자가 음량을 조절하지 않는다. */
export function PrepareDraftAudioMeter({ level }: { level: number }) {
  const active = Math.round(Math.min(1, Math.max(0, level)) * 48);
  return (
    <div
      className="prepare-draft-audio-meter"
      role="meter"
      aria-label="오디오 입력 레벨 예시"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round((active / 48) * 100)}
    >
      <svg
        width="20"
        height="20"
        viewBox="0 0 16 16"
        fill="currentColor"
        shapeRendering="crispEdges"
        aria-hidden="true"
      >
        <path d="M6 1h4v1h1v7h-1v1H6V9H5V2h1ZM2 6h2v4h1v1h1v1h4v-1h1v-1h1V6h2v4h-1v2h-2v1H9v2h3v1H4v-1h3v-2H5v-1H3v-2H2Z" />
      </svg>
      <div className="prepare-draft-audio-track" aria-hidden="true">
        {Array.from({ length: 48 }, (_, index) => (
          <span key={index} data-band={band(index)} data-active={index < active} />
        ))}
      </div>
    </div>
  );
}

function band(index: number) {
  if (index < 32) return 'green';
  if (index < 42) return 'amber';
  return 'brick';
}
