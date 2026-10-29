/**
 * 미리보기 iframe(PreviewFrame)과 그 안의 화면만 보기(FramePage)가 함께 쓰는 값.
 * 컴포넌트 파일에 두면 개발 서버의 빠른 새로고침이 컴포넌트만 바꿔 끼우지 못해 여기로 뺐다.
 */

/**
 * 미리보기 화면 크기(CSS px). 많이 쓰는 기기의 브라우저 안쪽 크기다. 칸보다 넓으면 줄여 보인다.
 * 크기만 맞춘다 — 터치 입력 · 모바일 브라우저의 주소창 · 확대 같은 동작은 재현하지 않는다.
 */
export const DEVICES = {
  fit: { label: '칸에 맞춤', width: null, height: null },
  'mobile-s': { label: '휴대폰 360 × 780', width: 360, height: 780 },
  mobile: { label: '휴대폰 390 × 844', width: 390, height: 844 },
  tablet: { label: '태블릿 768 × 1024', width: 768, height: 1024 },
  laptop: { label: '노트북 1280 × 800', width: 1280, height: 800 },
  'laptop-wide': { label: '노트북 1366 × 768', width: 1366, height: 768 },
  desktop: { label: '데스크톱 1440 × 900', width: 1440, height: 900 },
  wide: { label: '큰 화면 1920 × 1080', width: 1920, height: 1080 },
  custom: { label: '직접 입력', width: null, height: null },
} as const;
export type Device = keyof typeof DEVICES;

export function isDevice(value: string | null): value is Device {
  return value !== null && value in DEVICES;
}

/**
 * 브라우저 확대(%) — 크롬의 확대 · 축소 단계와 같다. Windows 배율(125% · 150%)도 같은 효과다.
 * 확대하면 페이지가 쓰는 폭 · 높이가 그만큼 줄어(1366 × 768을 125%로 보면 1093 × 614) 배치가 바뀐다
 */
export const ZOOMS = [50, 67, 75, 80, 90, 100, 110, 125, 150, 175, 200] as const;

/** 주소의 zoom(%)을 배율로. 목록에 없으면 100% */
export function previewZoom(params: URLSearchParams): number {
  const value = Number(params.get('zoom'));
  return (ZOOMS as readonly number[]).includes(value) ? value / 100 : 1;
}

/** 직접 입력의 범위. 이보다 작거나 크면 맞춰 넣는다 */
export const CUSTOM_LIMIT = { min: 280, max: 2560 };

/** 휴대폰 크기로 보는가. 이보다 좁으면 PC 전용 화면에 안내를 띄운다 */
export const PHONE_MAX_WIDTH = 767;

function clampSize(value: string | null, fallback: number) {
  const number = Math.round(Number(value));
  if (!Number.isFinite(number) || number <= 0) return fallback;
  return Math.min(CUSTOM_LIMIT.max, Math.max(CUSTOM_LIMIT.min, number));
}

/**
 * 주소의 device · w · h · rotate로 그릴 크기를 정한다. "칸에 맞춤"이면 null.
 * rotate는 가로 · 세로를 바꾼다(휴대폰 · 태블릿을 눕혀 볼 때)
 */
export function previewSize(params: URLSearchParams): { width: number; height: number } | null {
  const raw = params.get('device');
  const device: Device = isDevice(raw) ? raw : 'fit';
  let size: { width: number; height: number } | null = null;
  if (device === 'custom')
    size = { width: clampSize(params.get('w'), 1280), height: clampSize(params.get('h'), 800) };
  else {
    const preset = DEVICES[device];
    if (preset.width) size = { width: preset.width, height: preset.height };
  }
  if (size && params.get('rotate') === '1') size = { width: size.height, height: size.width };
  return size;
}

/** iframe ↔ 뷰어 메시지. 같은 출처의 그 iframe에서 온 것만 받는다 */
export const FRAME_MESSAGE = 'pitchcoach-design-frame';
export type FrameMessage =
  | { kind: typeof FRAME_MESSAGE; type: 'navigate'; screenId: string; stateId?: string }
  | { kind: typeof FRAME_MESSAGE; type: 'updated' };

/** 미리보기 iframe이 화면을 다시 그렸다(처음 · 저장 반영). 설명 패널의 디자인 값이 듣는다 */
export const FRAME_UPDATED_EVENT = 'design-guide:frame-updated';

/** 화면만 그리는 주소. 뷰어와 같은 문서 경로에 hash만 다르다 */
export function frameUrl(screenId: string, stateId: string, inline = false) {
  const params = new URLSearchParams({ screen: screenId, state: stateId });
  if (inline) params.set('inline', '1');
  const { origin, pathname, search } = window.location;
  return `${origin}${pathname}${search}#/frame?${params}`;
}
