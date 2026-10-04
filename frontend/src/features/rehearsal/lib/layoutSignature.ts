/**
 * 캘리브레이션 기준의 저장 키 — "같은 기기·같은 배치인가".
 *
 * ── 왜 FE 가 만드나 ─────────────────────────────────────────────────
 *
 * 분류기는 워커 안에 있어 화면 해상도·배율·카메라 기기를 알 수 없습니다.
 * AI v1 에도 이 개념이 없습니다. 그래서 메인 스레드가 만들고,
 * `ZoneReference` 를 IndexedDB 에 넣을 때 키로 붙입니다.
 *
 * ── 무엇을 담나 ─────────────────────────────────────────────────────
 *
 *   cam   카메라 기기. 다른 웹캠이면 렌즈 위치가 다릅니다
 *   v     영상 해상도. 같은 웹캠이라도 해상도가 바뀌면 화각이 달라질 수 있습니다
 *   s     화면 크기와 배율. 대본 자리(BOTTOM)가 어디인지를 정합니다
 *
 * 카메라를 화면 위에서 옆으로 옮긴 것은 여기서 잡히지 않습니다 —
 * 브라우저가 알 방법이 없습니다. 그래서 `loadZoneRef` 가 오래된 기준을 버립니다.
 *
 * ── deviceId 를 그대로 쓰지 않는 이유 ───────────────────────────────
 *
 * 이 값은 캘리브레이션 요약에 실려 서버로 갑니다. deviceId 는 브라우저가 이 사이트에
 * 주는 고정 식별자라, 원문을 보내면 기기 지문이 됩니다. 짧은 해시로 줄입니다 —
 * 같은 기기인지 가리는 데는 그걸로 충분합니다.
 */

export interface LayoutInputs {
  /** 비어 있으면 브라우저가 알려주지 않은 것입니다 (권한 전 등) */
  deviceId: string;
  videoWidth: number;
  videoHeight: number;
  screenWidth: number;
  screenHeight: number;
  devicePixelRatio: number;
}

/** FNV-1a 32비트. 보안용이 아니라 같은 값을 같은 짧은 문자열로 바꾸는 용도입니다 */
function shortHash(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, '0');
}

export function makeLayoutSignature(i: LayoutInputs): string {
  const cam = i.deviceId ? shortHash(i.deviceId) : 'unknown';
  // 배율은 소수 둘째 자리까지 — 1.25 와 1.2500001 이 다른 키가 되면 안 됩니다
  const dpr = Math.round(i.devicePixelRatio * 100) / 100;
  return `cam:${cam}|v${i.videoWidth}x${i.videoHeight}|s${i.screenWidth}x${i.screenHeight}@${dpr}`;
}

/** 지금 흐르고 있는 `<video>` 와 화면에서 읽습니다 */
export function readLayoutSignature(video: HTMLVideoElement): string {
  const track = (video.srcObject as MediaStream | null)?.getVideoTracks()[0];
  return makeLayoutSignature({
    deviceId: track?.getSettings().deviceId ?? '',
    videoWidth: video.videoWidth,
    videoHeight: video.videoHeight,
    screenWidth: window.screen.width,
    screenHeight: window.screen.height,
    devicePixelRatio: window.devicePixelRatio,
  });
}
