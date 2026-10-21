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
 */
export function toCalibrationRequest(
  summary: CalibrationSummary,
  { micDetected }: { micDetected: boolean },
): CalibrationRequest {
  const status = summary.quality === 'POOR' ? 'RETRY_REQUIRED' : 'OK';
  return {
    face_detected: true,
    mic_detected: micDetected,
    gaze_confidence: status === 'OK' ? (summary.looAccuracy ?? 0) : 0,
    result: {
      engine_version: summary.engineVersion,
      status,
      separability: summary.separability,
      loo_accuracy: summary.looAccuracy,
      warnings: summary.warnings,
    },
  };
}
