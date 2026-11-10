import type { ScreenBody } from '@/shared/lib/chatScreen';
import {
  CALIBRATION_FAIL_MESSAGE,
  type GazeSetupStatus,
  type SetupFailReason,
} from './useGazeSetup';

/**
 * #1185: 장치 점검 채팅은 #1171에서 의도적으로 숨깁니다.
 * AI 계약의 DeviceScreen 시나리오와 조율하기 전까지 이 변환 함수와 테스트는 보존하되
 * 화면에서 useChatScreen 으로 등록하지 않습니다. 정책 확정 후 제거/재사용을 결정합니다.
 */
/** 이 화면 상태가 읽는 것 — 장치 점검 화면의 점검 훅 상태 그대로입니다 */
export interface DeviceScreenSource {
  /** 카메라 영상이 들어오고 있나 (`useVideoStream` 의 `live`) */
  live: boolean;
  /** 장치를 열었나 — 마이크를 재기 시작했는지 가릅니다 */
  hasStream: boolean;
  /** 마이크 소리를 확인했나 (`useAudioMeter` 의 `micOk`) */
  micOk: boolean;
  gazeStatus: GazeSetupStatus;
  /** 시선 기준이 실패한 이유 — FAILED 일 때만 (`useGazeSetup` 의 `failReason`) */
  failReason: SetupFailReason | null;
}

/**
 * 장치 점검 화면의 상태 (12-4 F2 "화면 상태" 표, AI `DeviceScreen`). 장치 원본 · 식별자는 싣지 않습니다.
 *
 * - 마이크는 화면 줄과 같게 — 소리를 확인했으면 `detected`, 장치를 열고 재는 중이면 `checking`, 열기 전이면 `disconnected`.
 * - `gaze_reason` 은 화면에 실패 사유가 보일 때만, 화면에 보이는 그 문구 그대로입니다(지금 표시 중인 실패만)
 */
export function deviceScreen(s: DeviceScreenSource): Extract<ScreenBody, { name: 'device_check' }> {
  return {
    name: 'device_check',
    camera: s.live ? 'connected' : 'disconnected',
    mic: micState(s),
    gaze_setup: s.gazeStatus,
    gaze_reason:
      s.gazeStatus === 'FAILED' ? CALIBRATION_FAIL_MESSAGE[s.failReason ?? 'ENGINE_ERROR'] : null,
  };
}

function micState(
  s: Pick<DeviceScreenSource, 'hasStream' | 'micOk'>,
): 'detected' | 'checking' | 'disconnected' {
  if (s.micOk) return 'detected';
  if (s.hasStream) return 'checking';
  return 'disconnected';
}
