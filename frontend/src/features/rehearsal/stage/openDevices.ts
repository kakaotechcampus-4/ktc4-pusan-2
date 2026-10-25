import type { DeviceChoice, DeviceRequestOptions } from '../media/useCameraStream';

/**
 * 발표 화면의 카메라 · 마이크를 여는 순서. **React 를 모릅니다** — `useCameraStream` 의 두 함수를 받습니다.
 *
 * 1. 장치 점검에서 쓴 장치. 기본 장치를 열면 USB 마이크로 점검하고 내장 마이크로 녹음하는 일이 생깁니다
 * 2. 고른 카메라를 못 열면 기본 카메라를 시도합니다. 마이크는 점검한 장치를 유지하고
 *    장치가 없을 때만 기본 마이크로 넘어갑니다
 * 3. 그래도 못 열면 마이크만 엽니다 (고른 마이크 → 장치가 빠졌을 때만 기본 마이크). 카메라는 시작 조건이 아닙니다 (#445) —
 *    카메라 권한 거부 · 다른 앱이 사용 중이어도 발표는 하고 시선만 뺍니다. 못 연 이유는 `cameraError` 에 남습니다
 *
 * 마이크까지 못 열면 null 입니다. 무대는 `NO_MIC` 에서 멈춥니다 (`startGate`).
 */
export async function openStageDevices(
  devices: DeviceChoice,
  open: {
    request: (choice?: DeviceChoice, options?: DeviceRequestOptions) => Promise<MediaStream | null>;
    requestMic: (audioDeviceId?: string) => Promise<MediaStream | null>;
  },
): Promise<MediaStream | null> {
  const chosen = Boolean(devices.videoDeviceId || devices.audioDeviceId);
  return (
    (await open.request(devices)) ??
    (chosen
      ? await open.request({ audioDeviceId: devices.audioDeviceId }, { fallbackMic: true })
      : null) ??
    (await open.requestMic(devices.audioDeviceId))
  );
}
