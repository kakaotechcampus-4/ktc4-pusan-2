/**
 * 미리보기 iframe(PreviewFrame)과 그 안의 화면만 보기(FramePage)가 함께 쓰는 값.
 * 컴포넌트 파일에 두면 개발 서버의 빠른 새로고침이 컴포넌트만 바꿔 끼우지 못해 여기로 뺐다.
 */

/** 미리보기 화면 크기. 칸보다 넓으면 줄여 보인다 */
export const DEVICES = {
  fit: { label: '칸에 맞춤', width: null, height: null },
  mobile: { label: '모바일 375 × 812', width: 375, height: 812 },
  tablet: { label: '태블릿 768 × 1024', width: 768, height: 1024 },
  laptop: { label: '노트북 1280 × 800', width: 1280, height: 800 },
  desktop: { label: '데스크톱 1440 × 900', width: 1440, height: 900 },
} as const;
export type Device = keyof typeof DEVICES;

export function isDevice(value: string | null): value is Device {
  return value !== null && value in DEVICES;
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
