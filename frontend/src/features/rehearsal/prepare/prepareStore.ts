import { create } from 'zustand';

import type { CalibrationSummary, ScriptMode } from '@/types/api';

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

  setCalibration: (summary: CalibrationSummary) => void;
  declineGaze: () => void;
  setScriptMode: (mode: ScriptMode, byUser?: boolean) => void;
  reset: () => void;
}

const INITIAL = {
  calibration: null,
  gazeDeclined: false,
  scriptMode: 'HIGHLIGHT' as ScriptMode,
  scriptModeTouched: false,
};

export const usePrepareStore = create<PrepareState>((set) => ({
  ...INITIAL,
  setCalibration: (summary) => set({ calibration: summary, gazeDeclined: false }),
  declineGaze: () => set({ gazeDeclined: true, calibration: null }),
  setScriptMode: (mode, byUser = true) =>
    set((s) => ({ scriptMode: mode, scriptModeTouched: s.scriptModeTouched || byUser })),
  reset: () => set(INITIAL),
}));
