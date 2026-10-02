/**
 * 피치 생성 화면의 픽셀 아이콘. 시안의 도트 톤에 맞춰 정수 격자에 그립니다.
 * 전부 장식입니다 — 뜻은 옆의 글자가 전합니다.
 */

const svgProps = {
  'aria-hidden': true,
  focusable: false,
  shapeRendering: 'crispEdges',
} as const;

export function FolderIcon({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg {...svgProps} viewBox="0 0 16 16" className={className}>
      <path
        className="fill-none stroke-ink"
        strokeWidth="1.5"
        d="M1.75 3.75h4.5l1.5 1.5h6.5v7h-12.5z"
      />
    </svg>
  );
}

/** 왼쪽 접기 표시. 펼쳐져 있으면 아래, 접혀 있으면 오른쪽을 봅니다 */
export function CaretIcon({ open }: { open: boolean }) {
  return (
    <svg
      {...svgProps}
      viewBox="0 0 8 8"
      className={['h-2 w-2', open ? '' : '-rotate-90'].join(' ')}
    >
      <path className="fill-ink" d="M0 2h8v1h-1v1h-1v1h-1v1h-2v-1h-1v-1h-1v-1h-1z" />
    </svg>
  );
}

/** 오른쪽 꺾쇠. 본문에 띄운 갈래는 위(^), 나머지는 오른쪽(>) */
export function ChevronIcon({ up }: { up: boolean }) {
  return (
    <svg
      {...svgProps}
      viewBox="0 0 12 12"
      className={['h-3 w-3', up ? '-rotate-90' : ''].join(' ')}
    >
      <path className="fill-none stroke-ink" strokeWidth="1.5" d="M4.5 2.5l3.5 3.5l-3.5 3.5" />
    </svg>
  );
}

export function CalendarIcon() {
  return (
    <svg {...svgProps} viewBox="0 0 16 16" className="h-4 w-4">
      <path className="fill-none stroke-stone" strokeWidth="1.5" d="M2.75 3.75h10.5v9.5h-10.5z" />
      <path className="fill-stone" d="M2 6h12v1.5h-12zM5 2h1.5v3h-1.5zM9.5 2h1.5v3h-1.5z" />
    </svg>
  );
}

export function ClockIcon() {
  return (
    <svg {...svgProps} viewBox="0 0 16 16" className="h-4 w-4">
      <circle className="fill-none stroke-stone" strokeWidth="1.5" cx="8" cy="8" r="5.75" />
      <path className="fill-none stroke-stone" strokeWidth="1.5" d="M8 4.5v3.75h2.75" />
    </svg>
  );
}

/** 상단 발표 정보 줄의 고치기 표시 */
export function PencilIcon() {
  return (
    <svg {...svgProps} viewBox="0 0 16 16" className="h-3.5 w-3.5">
      <path
        className="fill-ink"
        d="M10 2h2v1h1v1h1v2h-1v1h-1v-1h-1v-1h-1v-1h-1v-1h1zM8 4h1v1h1v1h1v1h1v1h-1v1h-1v1h-1v1h-1v1h-1v1h-1v1h-4v-4h1v-1h1v-1h1v-1h1v-1h1v-1h1z"
      />
    </svg>
  );
}

/** 드롭존 가운데의 큰 PDF 아이콘. 접힌 귀퉁이 + 올리기 화살표 */
export function PdfUploadIcon() {
  return (
    <svg {...svgProps} viewBox="0 0 20 24" className="h-16 w-14">
      <path className="fill-line-strong" d="M3 3h12v1h1v1h1v1h1v17h-15z" />
      <path className="fill-panel" d="M2 2h11v5h5v15h-16z" />
      <path className="fill-ink" d="M1 1h12v1h-11v20h16v-15h1v16h-18z" />
      <path className="fill-ink" d="M13 1h1v1h1v1h1v1h1v1h1v1h1v1h-6z" />
      <path className="fill-coral" d="M9 9h2v1h1v1h1v1h-2v5h-2v-5h-2v-1h1v-1h1z" />
    </svg>
  );
}

export function FileIcon() {
  return (
    <svg {...svgProps} viewBox="0 0 12 14" className="h-3.5 w-3">
      <path className="fill-white" d="M0 0h8v1h1v1h1v1h1v1h1v10h-12z" />
      <path className="fill-coral" d="M3 7h6v1h-6zM3 9h6v1h-6zM3 11h4v1h-4z" />
    </svg>
  );
}

export function SparkleIcon() {
  return (
    <svg {...svgProps} viewBox="0 0 16 16" className="h-4 w-4">
      <path className="fill-coral" d="M6 2h2v3h1v1h3v2h-3v1h-1v3h-2v-3h-1v-1h-3v-2h3v-1h1z" />
      <path className="fill-coral" d="M12 10h1v1h1v1h-1v1h-1v-1h-1v-1h1z" />
    </svg>
  );
}

/** 사이드바 머리 오른쪽의 도트 장식 */
export function PixelMark() {
  return (
    <svg {...svgProps} viewBox="0 0 6 6" className="h-2.5 w-2.5">
      <path className="fill-ink" d="M3 0h3v3h-3zM0 3h3v3h-3z" />
    </svg>
  );
}
