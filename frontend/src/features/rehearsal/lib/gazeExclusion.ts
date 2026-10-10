import type { DeviceError } from '../media/useCameraStream';
import type { GazeExcludedReason } from '@/types/api';

/**
 * 발표 중 문제가 생겼을 때 **Take 전체의 시선을 제외할지**, 아니면 기록만 멈출지 정합니다.
 *
 * ── 카메라 끊김은 제외하지 않습니다 ───────────────────────────────────
 * AI 시선 계약(`ai/research/gaze-tracking/INTERFACE.md` 5절 · 4-1)에서 카메라가 끊기면 엔진은 기록을
 * 멈추고, 1초 기록이 오지 않은 시간은 **서버가 측정 못 함(UNMEASURED)으로 채웁니다.**
 * 그래서 끊기기 전까지 잘 잰 기록은 그대로 쓸 수 있습니다 (AI `DEPLOY.md` "끊기기 전까지 잘 잰 기록은 살리는 방식").
 * 전체를 빼면 9분을 잘 재다가 마지막에 잠깐 끊겨도 시선 리포트가 통째로 사라집니다.
 *
 * ── 그대로 제외하는 것 ───────────────────────────────────────────────
 *   카메라 권한 거부   사용자가 시선을 원하지 않았습니다 — 리포트에 그 이유(USER_DECLINED)가 남아야 합니다
 *   엔진이 못 뜨거나 죽음 · 기준 없음 · 저장 실패   앞의 기록부터 믿을 수 없거나 아예 없습니다
 */

/** 카메라 · 마이크 장치 오류. 권한 거부만 제외 사유이고, 장치가 없거나 끊긴 것은 기록만 멈춥니다 */
export function exclusionForDeviceError(error: DeviceError): GazeExcludedReason | null {
  return error === 'PERMISSION_DENIED' ? 'USER_DECLINED' : null;
}

/** 시선 워커 · 엔진 오류. 카메라 끊김(`CAMERA_LOST`)만 기록을 멈추는 것으로 끝냅니다 */
export function exclusionForGazeError(error: GazeExcludedReason): GazeExcludedReason | null {
  return error === 'CAMERA_LOST' ? null : error;
}
