import type {
  CalibrationFailReason,
  CalibrationResult,
  CameraPlacement,
  PlacementReason,
  PlacementResult,
} from './gaze.contract';

/**
 * AI v1 출력 → FE 계약. **옮기는 규칙은 이 파일에만 둡니다.**
 *
 * ── 왜 따로 있나 ────────────────────────────────────────────────────
 *
 * 아래 `Ai*` 타입은 AI 저장소(`ai/archive/workspaces/jewon-kim/gaze-tracking/v1`)의
 * Python 출력(`to_dict()`)을 필드 이름 그대로 옮긴 것입니다.
 * 브라우저 엔진(`src/vendor/gaze/engine`)도 같은 키로 냅니다 — 그쪽 `contract.ts` 가
 * 이 모양을 다시 적어 두고 타입 검사합니다.
 *
 * 규칙을 여기 모아 두면 AI 쪽 값이 바뀌었을 때 고칠 곳이 한 군데이고,
 * 워커 없이 테스트로 고정할 수 있습니다 (aiAdapter.test.ts).
 */

/** AI `CalibrationQuality.to_dict()` 에서 FE 가 읽는 것만 */
export interface AiCalibrationQuality {
  status: 'OK' | 'RETRY_REQUIRED';
  /** RETRY_REQUIRED 일 때만 값이 있습니다 */
  reason: string | null;
  /** 영어 안내문. 합격이어도 경고가 들어올 수 있습니다 */
  hint: string | null;
  separability: number;
  loo_accuracy: number;
}

/** AI `PlacementCheckResult.to_dict()` 에서 FE 가 읽는 것만 */
export interface AiPlacementCheckResult {
  placement: string;
  supported: boolean;
  reason: string;
}

/** AI `AiVersion` 과 `MODEL_VERSION` 에서 버전 문자열에 담는 것 */
export interface AiVersionParts {
  /** `MODEL_VERSION` — 예: `gaze_v1.1.0` */
  modelVersion: string;
  /** `AiVersion.gaze_backbone` — 예: `head_pose` */
  gazeBackbone: string;
  /** `AiVersion.gaze_classifier` — 예: `reference_anchor_v1` */
  gazeClassifier: string;
}

/**
 * 합격이지만 경고가 붙은 경우의 표식 (AI `INVERTED_PITCH_HINT_PREFIX`).
 * BOTTOM 을 볼 때 시선이 CAMERA 보다 아래로 가지 않았다는 뜻입니다.
 *
 * AI 문서가 `startsWith` 가 아니라 `includes` 로 찾으라고 적어 둡니다 —
 * 다른 문구 뒤에 붙어 올 수 있습니다.
 */
const INVERTED_PITCH_MARKER = 'INVERTED_PITCH';

/**
 * 합격이지만 화면이 렌즈에 합쳐진 경우의 표식 (AI `SCREEN_MERGED_WARNING`).
 * 렌즈와 화면 가운데를 고개로 구분하지 못해, 이 보정에서는 **화면을 본 시간도 청중으로** 셉니다.
 * 청중 비율이 부풀 수 있어 품질을 낮춥니다 — AI `web/README.md` 가 "GOOD 으로 보인다"고 짚은 부분입니다.
 * 막지는 않습니다(낮은 품질도 진행하는 AI 정책). 사용자에게는 카메라 화면의 결과 카드가 이미 알립니다.
 */
const SCREEN_MERGED_MARKER = 'SCREEN_MERGED';

/** 합격이지만 판정이 덜 믿을 만한 경고. 하나라도 있으면 FAIR 입니다 */
const FAIR_MARKERS = [INVERTED_PITCH_MARKER, SCREEN_MERGED_MARKER] as const;

const AI_FAIL_REASONS: readonly CalibrationFailReason[] = [
  'NOT_ENOUGH_SAMPLES',
  'CLASS_NOT_SEPARABLE',
  'LOW_LOO_ACCURACY',
  'CENTROIDS_TOO_CLOSE',
  'DEGENERATE_FEATURES',
  'ANCHOR_AMBIGUOUS',
];

/**
 * 캘리브레이션 품질 → `CalibrationResult`.
 *
 * AI 는 품질 검사와 모델 학습을 따로 봅니다 — 검사에 떨어져도 두 클래스가 한 장씩만
 * 있으면 모델은 학습합니다 (`PerUserGazeClassifier.fit`). 그래서 셋으로 나뉩니다.
 *
 *   모델 없음(model === null)        → ok: false   재시도 (AI 데모의 "사용 불가")
 *   검사 통과(status OK)              → GOOD / FAIR
 *   검사 불합격 + 모델 있음            → POOR + advice   진행 가능 (AI 데모의 "낮음 · 재측정 권장")
 *
 * `model` 은 분류기가 학습한 값이라 호출부가 넘깁니다 — 여기서는 열어 보지 않습니다.
 * AI 의 `is_fitted` 가 false 면 null 을 넘겨 주세요.
 *
 * 모르는 실패 사유가 오면 `ENGINE_ERROR` 로 둡니다. 재시도 안내는 나가야 하고,
 * AI 가 사유를 늘렸다는 신호이기도 합니다.
 */
export function toCalibrationResult(q: AiCalibrationQuality, model: unknown): CalibrationResult {
  const reason = AI_FAIL_REASONS.find((r) => r === q.reason) ?? 'ENGINE_ERROR';
  if (model === null) return { ok: false, reason: q.status === 'OK' ? 'ENGINE_ERROR' : reason };

  const metrics = { separability: q.separability, looAccuracy: q.loo_accuracy };
  if (q.status !== 'OK') {
    return { ok: true, ref: { quality: 'POOR', metrics, model }, advice: reason };
  }

  return {
    ok: true,
    ref: {
      quality: FAIR_MARKERS.some((m) => q.hint?.includes(m)) ? 'FAIR' : 'GOOD',
      metrics,
      model,
    },
    advice: null,
  };
}

/**
 * 버전 문자열. Take 에 영구 고정되므로 순서를 바꾸지 마세요.
 *
 * AI 카메라 화면 모듈의 `ready.version` 과 **같은 문자열**입니다 (AI `INTERFACE.md` 8절).
 * 장치 점검은 그 값으로 기준을 저장하고 리허설 워커는 이 값으로 꺼내므로,
 * 둘이 한 글자라도 다르면 방금 잡은 기준을 못 찾습니다.
 */
export function engineVersion(v: AiVersionParts): string {
  return `${v.modelVersion}+${v.gazeBackbone}+${v.gazeClassifier}`;
}

const PLACEMENTS: readonly CameraPlacement[] = [
  'TOP',
  'BOTTOM',
  'SIDE_LEFT',
  'SIDE_RIGHT',
  'INCONCLUSIVE',
];

const PLACEMENT_REASONS: readonly PlacementReason[] = [
  'OK',
  'NOT_ENOUGH_SAMPLES',
  'TARGETS_NOT_SEPARATED',
  'DISPLACEMENT_TOO_SMALL',
  'AMBIGUOUS_AXIS',
];

/**
 * 카메라 배치 → `PlacementResult`.
 *
 * 모르는 위치는 INCONCLUSIVE, 모르는 사유는 ENGINE_ERROR 로 둡니다.
 * `supported` 는 AI 값을 믿되 TOP 이 아니면 false 로 고정합니다 —
 * 두 값이 어긋나면 "지원됨"이라고 말하는 쪽이 더 위험합니다.
 */
export function toPlacementResult(r: AiPlacementCheckResult): PlacementResult {
  const placement = PLACEMENTS.find((p) => p === r.placement) ?? 'INCONCLUSIVE';
  const reason = PLACEMENT_REASONS.find((x) => x === r.reason) ?? 'ENGINE_ERROR';
  return { placement, supported: r.supported && placement === 'TOP', reason };
}
