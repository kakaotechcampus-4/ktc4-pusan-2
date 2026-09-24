const glyphs: Record<string, string[]> = {
  P: ['11110', '11011', '11011', '11110', '11000', '11000', '11000'],
  I: ['11111', '00100', '00100', '00100', '00100', '00100', '11111'],
  T: ['11111', '11111', '00100', '00100', '00100', '00100', '00100'],
  C: ['01111', '11000', '11000', '11000', '11000', '11000', '01111'],
  H: ['11011', '11011', '11011', '11111', '11011', '11011', '11011'],
  O: ['01110', '11011', '11011', '11011', '11011', '11011', '01110'],
  A: ['01110', '11011', '11011', '11111', '11011', '11011', '11011'],
};

function wordPath(word: string, offset: number) {
  return [...word]
    .flatMap((letter, index) =>
      glyphs[letter].flatMap((row, y) =>
        [...row].flatMap((pixel, x) =>
          pixel === '1' ? [`M${offset + index * 6 + x} ${y}h1v1h-1z`] : [],
        ),
      ),
    )
    .join('');
}

const pitch = wordPath('PITCH', 0);
const coach = wordPath('COACH', 34);

/** A font-independent 5×7 pixel wordmark, drawn on a shared integer grid. */
export function PitchCoachWordmark() {
  return (
    <svg
      viewBox="0 0 64 9"
      width="192"
      height="27"
      aria-hidden="true"
      focusable="false"
      shapeRendering="crispEdges"
      className="block shrink-0"
    >
      <path d={pitch + coach} transform="translate(1 1)" className="fill-line-strong" />
      <path d={pitch} className="fill-ink" />
      <path d={coach} className="fill-coral-deep" />
    </svg>
  );
}
