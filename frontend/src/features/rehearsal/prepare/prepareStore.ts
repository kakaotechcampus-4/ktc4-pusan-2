import { create } from 'zustand';

import type { DeviceChoice } from '../media/useCameraStream';
import type { CalibrationSummary, Mode, ScriptMode } from '@/types/api';

/**
 * 서버가 준 대본 표시 값을 지금 쓰는 3단계로 접습니다.
 *
 * 없어진 `FULL`(전체 대본)은 HIGHLIGHT 로 갑니다 — 보이는 글도 영역 높이도 같았고,
 * 강조가 붙느냐만 달랐습니다. 모르는 값이 와도 HIGHLIGHT 로 떨어집니다:
 * 대본을 아예 안 띄우는 것(OFF)으로 잘못 떨어지면 발표자가 말을 잃습니다.
 *
 * ★ BE 가 아직 FULL 을 보낼 수 있어서 필요합니다. 계약이 3단계로 맞춰지면 지웁니다.
 */
export function normalizeScriptMode(raw: string | null | undefined): ScriptMode {
  if (raw === 'KEYWORD' || raw === 'OFF' || raw === 'HIGHLIGHT') return raw;
  return 'HIGHLIGHT';
}

/**
 * 고른 대본 표시에서 연습 모드를 정합니다.
 *
 * 시안 09 가 `실전 모드 - 대본 없이` 를 한 줄로 묶었습니다. 그래서 화면에서는
 * 하나만 고르고, 두 계약 필드(`mode` / `script_mode`)는 여기서 같이 정해집니다.
 *
 * 대가가 있습니다 - **"대본만 끄고 코칭은 받기"가 없어집니다.** 실전 모드는 대본만
 * 끄는 것이 아니라 발표 중 코치를 통째로 침묵시킵니다 (CLAUDE.md 4번). 둘을 따로
 * 고르게 하려면 시안과 화면이 달라져야 해서, 시안을 따르기로 했습니다.
 *
 * 계약에서 Mode 와 ScriptMode 는 여전히 별개입니다 - 서버로는 두 값이 그대로 갑니다.
 * 나중에 둘을 따로 고르게 되돌리려면 이 함수만 지우면 됩니다.
 */
export function modeForScriptMode(scriptMode: ScriptMode): Mode {
  return scriptMode === 'OFF' ? 'EXAM' : 'COACHING';
}

/**
 * 장치 점검 → 리허설 준비로 넘어가는 동안 들고 가야 하는 것들.
 *
 * 왜 Zustand인가 — 두 화면에 걸쳐 살아야 하고(라우팅으로 컴포넌트가 죽습니다),
 * 서버에서 온 값이 아니며(그건 TanStack Query), 프레임 단위도 아닙니다(그건 ref).
 * 상태 배치표의 가운데 칸입니다.
 *
 * 카메라 스트림은 여기 두지 않습니다. 화면이 죽을 때 놓아주지 않으면
 * 카메라 표시등이 안 꺼집니다 — 그건 useCameraStream이 언마운트에서 처리합니다.
 */
interface PrepareState {
  /** 2점 캘리브레이션 결과. 시작 CTA에서 POST /takes/{id}/calibration으로 갑니다 */
  calibration: CalibrationSummary | null;
  /** '소리만으로 계속하기' — 시선 없이 진행합니다 (excludedReason: USER_DECLINED) */
  gazeDeclined: boolean;
  scriptMode: ScriptMode;
  /** 사용자가 직접 골랐나. 서버 기본값으로 덮어쓰지 않기 위한 표시입니다 */
  scriptModeTouched: boolean;
  /**
   * 점검을 통과한 카메라·마이크. 리허설이 **같은 장치**를 엽니다.
   * 비어 있으면 브라우저 기본 장치입니다 — 캘리브레이션과 녹음이 다른 장치에서
   * 나오면 점검이 아무것도 보장하지 못합니다.
   */
  devices: DeviceChoice;

  setCalibration: (summary: CalibrationSummary) => void;
  declineGaze: () => void;
  setScriptMode: (mode: ScriptMode, byUser?: boolean) => void;
  setDevices: (devices: DeviceChoice) => void;
  reset: () => void;
}

const INITIAL = {
  calibration: null,
  gazeDeclined: false,
  scriptMode: 'HIGHLIGHT' as ScriptMode,
  scriptModeTouched: false,
  devices: {} as DeviceChoice,
};

export const usePrepareStore = create<PrepareState>((set) => ({
  ...INITIAL,
  setCalibration: (summary) => set({ calibration: summary, gazeDeclined: false }),
  declineGaze: () => set({ gazeDeclined: true, calibration: null }),
  setScriptMode: (mode, byUser = true) =>
    set((s) => ({ scriptMode: mode, scriptModeTouched: s.scriptModeTouched || byUser })),
  setDevices: (devices) => set({ devices }),
  reset: () => set(INITIAL),
}));
