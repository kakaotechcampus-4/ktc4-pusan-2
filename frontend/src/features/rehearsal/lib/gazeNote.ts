/**
 * 무대 발밑 줄의 시선 안내 한 줄. **시선이 기록되지 않을 때만** 띄웁니다 — 정상일 때는 비워 둡니다.
 *
 * 발표 중에 고칠 수 없는 문제(권한 거부 · 기준 없음 · 엔진 오류)도 한 줄은 남깁니다. 코치 상태 표시가
 * 시선을 '—' 로 보여 주긴 하지만 그건 코칭 모드에서 소켓이 붙어 있을 때만 옵니다. 실전 모드이거나
 * 끊겨 있으면 아무 표시가 없어, 남은 발표의 시선이 빠진 것을 리포트에서야 알게 됩니다.
 *
 * 정상일 때 "시선 기록 중"을 띄우지 않는 이유 — 발표 중에 읽을 글자를 줄입니다. 문구는 오류 코드가
 * 아니라 사용자가 읽을 문장입니다.
 *
 * 기록이 멈춘 상태는 고칠 수 있든 없든 모두 경고로 둡니다 (#235). 일반 상태 문구와 같은 모양이면
 * 발표 중에 놓치고, 그 뒤 시선이 빠진 것을 리포트에서야 알게 됩니다. 준비 중만 경고가 아닙니다.
 */
import type { GazeExcludedReason } from '@/types/api';

export interface GazeNote {
  text: string;
  /** 시선 기록이 멈췄습니다 — 무대가 경고로 눈에 띄게 그립니다 */
  alert: boolean;
}

export function gazeNote(state: {
  /** 카메라 권한을 거부했습니다 */
  declined: boolean;
  /** 카메라를 열지 못해 마이크만 열었습니다 */
  noCamera: boolean;
  /** 카메라가 끊겼습니다 — 발표 중에는 다시 열지 않습니다 */
  cameraLost: boolean;
  /** 시선 기준이 없습니다 (새로고침하면 탭 메모리의 기준이 사라집니다) */
  missingCalibration: boolean;
  /** 시선 엔진이 알린 제외 사유 */
  error: GazeExcludedReason | null;
  /** 엔진이 떠서 판정하고 있습니다 */
  ready: boolean;
}): GazeNote | null {
  if (state.declined || state.error === 'USER_DECLINED') {
    return { text: '카메라 권한이 없어 시선은 기록하지 않아요', alert: true };
  }
  if (state.noCamera) {
    return { text: '카메라를 열지 못해 시선은 기록하지 않아요', alert: true };
  }
  // 끊기기 전 기록은 남습니다. 발표 중에는 카메라를 다시 열지 않아서 다시 꽂아도 이후 시선은 빠집니다
  if (state.cameraLost || state.error === 'CAMERA_LOST') {
    return { text: '카메라가 끊겨 이후 시선은 기록하지 않아요', alert: true };
  }
  // 그 전 기록은 남고, 이후는 서버가 측정 못 함으로 셉니다
  if (state.missingCalibration || state.error === 'CALIBRATION_LOST') {
    return { text: '시선 기준이 없어 이후 시선은 기록하지 않아요', alert: true };
  }
  if (state.error === 'ENGINE_UNAVAILABLE') {
    return { text: '시선 분석을 쓸 수 없어 시선은 기록하지 않아요', alert: true };
  }
  // 그 밖의 제외 사유는 사용자가 할 일이 없고 이름도 어렵습니다. 기록되지 않는다는 것만 알립니다
  if (state.error !== null) return { text: '시선은 기록하지 않아요', alert: true };
  if (!state.ready) return { text: '시선 분석을 준비하는 중이에요', alert: false };
  return null;
}
