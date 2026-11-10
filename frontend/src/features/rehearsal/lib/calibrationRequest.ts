import type { CalibrationSummary } from '@/types/api';
import type { CalibrationRequest } from '@/types/take';

/**
 * FE 보정 요약 → 서버로 보내는 발표 전 점검 결과. **옮기는 규칙은 여기에만 둡니다.**
 *
 * 모양은 AI 시선 문서(`docs/ai/features/gaze-tracking.md` 12-1)의 `calibrations` 입니다.
 *
 *   gaze_confidence  보정이 OK 면 `loo_accuracy`, 아니면 0 (문서 15절의 추천)
 *   result           `{engine_version, status, separability, loo_accuracy, warnings}`
 *
 * `status` 는 등급에서 되돌립니다. POOR 는 AI 검사에 떨어진(RETRY_REQUIRED) 보정이고,
 * GOOD · FAIR 는 통과(OK)입니다 — FAIR 는 통과에 경고가 붙은 것입니다 (`aiAdapter.ts`).
 *
 * 얼굴은 보정을 마쳤으니 잡힌 것이고, 마이크는 장치 점검이 확인한 값을 받습니다.
 * 기준 목소리 크기는 장치 점검이 정한 값을 그대로 옮깁니다 — 이번에 잰 값이나 같은 환경에서 다시 쓴 값,
 * 탈출구로 시작하면 null 입니다 (#1179).
 *
 * **시선 보정 요약이 없어도 보냅니다** (#1179). 서버의 `result` 는 선택 칸이라 null 로 두고, 얼굴 · 시선 신뢰도는
 * 잡히지 않은 것으로 둡니다. 전에는 요약이 없으면 요청을 보내지 않아 목소리 기준까지 빠졌습니다.
 */
export function toCalibrationRequest(
  summary: CalibrationSummary | null,
  { micDetected, baseLevelDb }: { micDetected: boolean; baseLevelDb: number | null },
): CalibrationRequest {
  if (!summary) {
    return {
      face_detected: false,
      mic_detected: micDetected,
      gaze_confidence: 0,
      base_level_db: baseLevelDb,
      result: null,
    };
  }
  const status = summary.quality === 'POOR' ? 'RETRY_REQUIRED' : 'OK';
  return {
    face_detected: true,
    mic_detected: micDetected,
    gaze_confidence: status === 'OK' ? (summary.looAccuracy ?? 0) : 0,
    base_level_db: baseLevelDb,
    result: {
      engine_version: summary.engineVersion,
      status,
      separability: summary.separability,
      loo_accuracy: summary.looAccuracy,
      warnings: summary.warnings,
    },
  };
}
