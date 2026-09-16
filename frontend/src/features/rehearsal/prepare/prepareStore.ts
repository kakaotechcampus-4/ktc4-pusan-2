import { create } from 'zustand';

/**
 * ★ 시선 캘리브레이션이 아직 붙지 않았습니다.
 *
 * 분류기 계약(A안)의 `fitCalibration` 은 4초 동안 모은 **프레임**을 받아
 * 기준과 품질을 분류기가 계산해 돌려줍니다. 그 경로에 맞춘 화면 배선은
 * 다음 브랜치에서 합니다.
 *
 * 그때까지 05 는 안내만 하고, 06 은 기준 없이도 시작할 수 있게 둡니다 —
 * 아니면 뒤 화면을 아무도 못 봅니다.
 *
 * 붙이고 나면 이 상수와 이걸 보는 두 곳(05·06)을 지웁니다.
 */
export const GAZE_CALIBRATION_WIRED = false;
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
