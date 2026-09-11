import { compressToSegments, summarize, validate } from './gazeSegments';
import type { CalibrationSummary, GazeExcludedReason, GazePayload, Ms } from '@/types/api';
import type { ZoneDecision } from '@/workers/gaze.contract';

/**
 * 1초 판정들을 서버로 보낼 `GazePayload` 로 조립합니다.
 *
 * ★ 이 값은 **종료 시점에 영구 고정됩니다.** 카메라 영상이 서버로 가지 않으므로
 *   서버는 시선을 재계산할 수 없고, 과거 Take 를 다시 분석할 수도 없습니다
 *   (CLAUDE.md 1번). 그래서 engineVersion 과 measuredMs 가 필수입니다.
 *
 * ── null 규칙의 위치 ────────────────────────────────────────────────
 *
 * 헷갈리기 쉬운 부분입니다. `null` 은 **응답**(`ReportGaze`)의 규칙입니다:
 *   ReportGaze.cameraMs: Ms | null      ← 제외 시 null. 0 이면 "청중을 0% 봤습니다"
 *   GazePayload.trackedMs: Ms           ← 요청은 null 을 받지 않습니다
 *
 * 그래서 제외된 Take 는 `excluded: true` + 빈 구간 + 0 을 보내고,
 * **서버가 그것을 null 로 바꿔 리포트에 담습니다.** 여기서 0 을 보내는 것은
 * "0% 봤다"가 아니라 "측정한 구간이 없다"는 뜻이고, `excluded` 가 그 구분을 합니다.
 */

/** 엔진 버전을 모를 때 보낼 값 — 로드 자체가 실패한 경우입니다. */
export const ENGINE_VERSION_UNAVAILABLE = 'unavailable';

export interface BuildGazePayloadInput {
  decisions: ZoneDecision[];
  /** 발표 전체 길이. 구간이 이걸 넘으면 검증에서 걸립니다 */
  durationMs: Ms;
  /** 워커의 ready 에서 받은 값. 엔진이 못 떴으면 null */
  engineVersion: string | null;
  decisionIntervalMs: Ms;
  /** 이미 정해진 제외 사유 (엔진 실패·카메라 소실·권한 거부 등) */
  excludedReason: GazeExcludedReason | null;
  calibration: CalibrationSummary | null;
}

export interface BuildGazePayloadResult {
  payload: GazePayload;
  /** 자체 검증에서 걸린 것. 비어 있지 않으면 payload 는 제외로 바뀝니다 */
  validationErrors: string[];
  /** 화면에 시선 숫자를 보여도 되는지 — 제외면 숫자 대신 "측정 제외"를 띄웁니다 */
  cameraMs: Ms | null;
  bottomMs: Ms | null;
}

/**
 * `engineProfile` 은 지금 항상 NORMAL 입니다.
 * LIGHT/OFF 로 내리는 임계값은 I-03(백본·프레임당 ms)이 정해져야 만들 수 있습니다.
 * 임의로 확정하지 않습니다.
 */
export function buildGazePayload(input: BuildGazePayloadInput): BuildGazePayloadResult {
  const { decisions, durationMs, engineVersion, decisionIntervalMs, calibration } = input;

  const excludedPayload = (reason: GazeExcludedReason): GazePayload => ({
    engineVersion: engineVersion ?? ENGINE_VERSION_UNAVAILABLE,
    engineProfile: 'NORMAL',
    decisionIntervalMs,
    excluded: true,
    excludedReason: reason,
    // 측정한 구간이 없다는 뜻입니다. "0% 봤다"가 아닙니다 — excluded 가 그 구분을 합니다.
    trackedMs: 0,
    measuredMs: 0,
    uncertainMs: 0,
    calibration,
    segments: [],
  });

  // 이미 제외가 정해진 경우 — 구간을 계산하지 않습니다. 신뢰할 수 없는 숫자를
  // 굳이 만들어 보내면, 나중에 excluded 를 놓친 화면이 그걸 그려 버립니다.
  if (input.excludedReason) {
    return {
      payload: excludedPayload(input.excludedReason),
      validationErrors: [],
      cameraMs: null,
      bottomMs: null,
    };
  }

  const segments = compressToSegments(decisions, decisionIntervalMs);
  const validationErrors = validate(segments, durationMs);

  // 보내기 전에 스스로 걸러냅니다. 서버가 422 INVALID_SEGMENTS 로 되돌려주기 전에
  // 여기서 잡는 편이 낫습니다 — 되돌려받아도 재계산할 방법이 없습니다.
  if (validationErrors.length > 0) {
    return {
      payload: excludedPayload('VALIDATION_FAILED'),
      validationErrors,
      cameraMs: null,
      bottomMs: null,
    };
  }

  const totals = summarize(segments);

  return {
    payload: {
      engineVersion: engineVersion ?? ENGINE_VERSION_UNAVAILABLE,
      engineProfile: 'NORMAL',
      decisionIntervalMs,
      excluded: false,
      excludedReason: null,
      trackedMs: totals.trackedMs,
      measuredMs: totals.measuredMs,
      uncertainMs: totals.uncertainMs,
      calibration,
      segments,
    },
    validationErrors: [],
    cameraMs: totals.cameraMs,
    bottomMs: totals.bottomMs,
  };
}
