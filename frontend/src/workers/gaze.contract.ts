/**
 * ============================================================
 *  시선 분류기 계약 — W4에서 고정하고, 이후 바꾸지 않습니다.
 * ============================================================
 *
 * 왜 이 파일이 따로 있는가:
 *   AI팀의 학습된 모델은 W7쯤 옵니다. 그때까지 FE는 더미로 갑니다.
 *   여기 정의된 모양을 지키면 실모델 교체가 파일 하나 갈아끼우는 일이 됩니다.
 *   이 계약을 안 정하고 더미를 만들면, 모델이 왔을 때 리허설 화면 전체를 다시 만집니다.
 *   W4 스파이크의 진짜 목적이 fps 측정이 아니라 이겁니다.
 *
 * 지켜야 할 경계:
 *   프레임은 워커로 들어가고, 1초 판정만 나옵니다.
 *   원시 좌표를 메인 스레드로 넘기기 시작하면 그 순간 성능이 끝납니다.
 *
 * ── 분업 (A안) ──────────────────────────────────────────────────────
 *
 *   AI  전처리 · 얼굴검출 · 특징추출 · 프레임 판정 · 캘리브레이션 계산
 *   FE  카메라 · 프레임 펌프 · 1초 다수결 · 저장 · 4초 안내 화면
 *
 * 그래서 `classify` 가 특징 벡터가 아니라 **ImageBitmap** 을 받고,
 * `fitCalibration` 이 기준값을 **돌려줍니다**(받지 않습니다).
 *
 * 1초 다수결을 AI 쪽에도 두면 이중 평활이 걸려 반응이 두 배로 느려집니다.
 * 분류기는 프레임 단위 판정만 냅니다.
 */

import type { GazeZone, Ms } from '@/types/api';

/**
 * 캘리브레이션 결과. **서버로 보내지 않습니다** — IndexedDB 에만 둡니다.
 * 서버에는 품질 요약(`CalibrationSummary`)만 갑니다.
 *
 * ── 왜 이 모양인가 ──────────────────────────────────────────────────
 *
 * 이전에는 `camera`·`bottom` 2점 벡터와 `coordinateSpace` 가 있었습니다.
 * 그건 **분류기 구현 세부가 FE 타입으로 새어 나온 것**이었습니다 —
 * 시선을 3차원 벡터로 본다는 가정, 기준이 정확히 2점이라는 가정이 들어 있었습니다.
 * 그런 것은 `model` 안에 둡니다. FE 는 열어 보지 않습니다.
 *
 * `metrics` 는 다릅니다. AI(v1 `CalibrationQuality`)가 실제로 내는 **요약 수치**이고,
 * FE 는 해석하지 않고 리포트용 요약에 옮겨 담기만 합니다.
 *
 * `layoutSignature` 는 여기 없습니다 — 워커 안의 분류기는 화면 해상도·배율·
 * 카메라 기기를 알 수 없습니다. 메인 스레드가 만들어 저장 키로 씁니다
 * (`features/rehearsal/lib/layoutSignature.ts`).
 */
export interface ZoneReference {
  /**
   * 화면에 보여줄 등급 (`aiAdapter.ts`).
   *
   *   GOOD  AI 품질 검사 통과
   *   FAIR  통과했지만 경고가 붙음 (INVERTED_PITCH)
   *   POOR  검사는 떨어졌지만 **모델은 학습됨** — 쓸 수는 있고, 다시 잡기를 권합니다
   *
   * POOR 를 막지 않는 것은 AI 정책입니다. AI 데모는 "분류기 부재만이 실제 차단 요인"이라
   * 품질이 낮으면 경고만 하고 진행합니다. 모델이 아예 없으면 `ok: false` 입니다.
   */
  quality: 'GOOD' | 'FAIR' | 'POOR';
  /**
   * AI 가 낸 품질 수치. **모르면 null 입니다** (더미처럼) —
   * 0 으로 채우면 "분리도가 0" 이라는 뜻이 되어 리포트가 거짓말을 합니다.
   */
  metrics: {
    /** 두 기준의 중심 거리 ÷ 클래스 내 퍼짐 (AI `separability`) */
    separability: number;
    /** 캘리브레이션 프레임 leave-one-out 정확도 (AI `loo_accuracy`) */
    looAccuracy: number;
  } | null;
  /**
   * 분류기가 학습한 것. **FE 는 해석하지 않습니다** — 저장하고 되돌려주기만 합니다.
   *
   * IndexedDB 에 넣고 다음 Take 에서 되살려야 하므로
   * **구조화 복제(structured clone)가 되는 값**이어야 합니다.
   */
  model: unknown;
}

/**
 * 캘리브레이션을 다시 받아야 하는 이유.
 *
 * 앞의 여섯은 AI v1.1 `FailReason` 에서 실제로 나오는 값과 같은 문자열입니다
 * (`ANCHOR_AMBIGUOUS` 는 v1.1 에서 생겼습니다 — 3점 기준 중 하나가 주변과 구분되지 않음).
 * `ENGINE_ERROR` 만 FE 몫입니다 — 분류기가 예외를 던졌거나 워커가 없을 때입니다.
 *
 * AI 는 영어 `hint` 도 주지만 받지 않습니다. 문구는 화면이 사유별로 가집니다.
 */
export type CalibrationFailReason =
  | 'NOT_ENOUGH_SAMPLES'
  | 'CLASS_NOT_SEPARABLE'
  | 'LOW_LOO_ACCURACY'
  | 'CENTROIDS_TOO_CLOSE'
  | 'DEGENERATE_FEATURES'
  | 'ANCHOR_AMBIGUOUS'
  | 'ENGINE_ERROR';

/**
 * `fitCalibration` 의 결과.
 *
 *   ok: true   모델이 있습니다. 품질이 낮으면(POOR) `advice` 에 왜 다시 잡는 게 나은지가 옵니다
 *   ok: false  모델을 만들 수 없었습니다 — 한쪽을 본 프레임이 하나도 없었거나 엔진이 멈췄습니다
 */
export type CalibrationResult =
  | { ok: true; ref: ZoneReference; advice: CalibrationFailReason | null }
  | { ok: false; reason: CalibrationFailReason };

/**
 * 카메라가 화면의 어디에 붙어 있나 — 발표자 쪽에서 본 위치 (AI v1 `CameraPlacement`).
 *
 * CAMERA/BOTTOM 판정은 **웹캠이 화면 위에 있다**는 전제 위에 서 있습니다.
 * 아래나 옆에 있으면 판정이 흐려지는 게 아니라 **조용히 뒤집힙니다** —
 * 대본을 볼 때 시선이 렌즈보다 위로 가기 때문입니다. 그래서 캘리브레이션 전에 봅니다.
 */
export type CameraPlacement = 'TOP' | 'BOTTOM' | 'SIDE_LEFT' | 'SIDE_RIGHT' | 'INCONCLUSIVE';

/**
 * 판정 근거 (AI v1 `PlacementReason`). `ENGINE_ERROR` 만 FE 몫입니다 —
 * 분류기가 예외를 던졌거나 워커가 없을 때입니다.
 */
export type PlacementReason =
  | 'OK'
  | 'NOT_ENOUGH_SAMPLES'
  | 'TARGETS_NOT_SEPARATED'
  | 'DISPLACEMENT_TOO_SMALL'
  | 'AMBIGUOUS_AXIS'
  | 'ENGINE_ERROR';

/**
 * 카메라 배치 확인 결과. **참고용 추정입니다** — 사용자가 안내 지점을 잘못 보면
 * 틀립니다. 그래서 화면은 경고만 하고, 캘리브레이션을 막지 않습니다
 * (AI 데모도 같은 정책입니다).
 */
export interface PlacementResult {
  placement: CameraPlacement;
  /** TOP 일 때만 true — CAMERA/BOTTOM 모델이 만들어진 배치입니다 */
  supported: boolean;
  reason: PlacementReason;
}

/**
 * 프레임 **하나**에 대한 판단. 1초 다수결은 여기서 하지 않습니다.
 *
 * 왜 나눴는가 — 1초 다수결은 모델의 일이 아니라 우리 정책입니다.
 * 분류기 안에 두면 모델을 갈아끼울 때 정책이 같이 사라집니다.
 * 엔진 버전 문자열에 `vote-v1`이 별도 부품으로 적히는 것도 같은 이유입니다.
 * 다수결은 workers/temporalVoter.ts 가 맡습니다.
 *
 * AI v1 에서는 **평활화 전** 프레임 판정(`GazeDecision`)이 여기로 옵니다.
 * AI 의 `TemporalSmoother`(`GAZE_STATE` 이벤트)는 쓰지 않습니다 —
 * 두 번 평활하면 반응이 두 배로 느려집니다. 옮기는 규칙은 `aiAdapter.ts` 에 있습니다.
 *
 *   zone        CAMERA · BOTTOM · UNCERTAIN(분류기가 기권한 프레임)
 *   confidence  두 클래스 확률 중 큰 쪽 (AI `p_max`)
 */
export interface FrameVerdict {
  zone: GazeZone;
  confidence: number;
}

/** 1초마다 워커가 메인으로 보내는 것. 이것만 나갑니다. */
export interface ZoneDecision {
  tMs: Ms;
  zone: GazeZone;
  confidence: number;
  /** 이 1초 동안 실제로 얼굴이 잡힌 프레임 수 — 신뢰도의 근거 */
  sampleCount: number;
}

/**
 * 실모델이 오면 이 인터페이스만 구현하면 됩니다.
 * 지금은 DummyGazeClassifier가, 나중에는 OnnxGazeClassifier가 들어옵니다.
 */
export interface GazeClassifier {
  /** 모델 가중치 로드. 실패하면 throw — 호출부가 excludedReason으로 바꿉니다 */
  init(): Promise<void>;
  /**
   * 캘리브레이션 4초분 프레임에서 그 사용자의 기준을 **계산합니다.**
   *
   * 왜 분류기가 계산하나 — 같은 절대 시선 각도가 사람마다·카메라 위치마다
   * 다른 의미입니다. 판별에 쓰이는 값은 그 사용자 자신의 CAMERA·BOTTOM
   * 기준으로부터의 상대 위치이고, 그걸 아는 건 분류기뿐입니다.
   *
   * `ok: false` 면 품질 미달입니다 — 화면은 `reason` 에 맞는 재시도 안내를 띄웁니다.
   * 4초 안내 화면(P4)은 FE 가 만듭니다. 계산만 여기서 합니다.
   */
  fitCalibration(camera: readonly ImageBitmap[], bottom: readonly ImageBitmap[]): CalibrationResult;
  /**
   * 카메라 배치를 추정합니다. **캘리브레이션 전에 한 번** 부릅니다.
   *
   * `camera` 는 렌즈를 본 2초, `screen` 은 **모니터 한가운데**를 본 2초입니다.
   * 화면 가운데가 렌즈보다 아래에 있으면 카메라가 위(TOP)에 있는 것입니다.
   */
  checkPlacement(camera: readonly ImageBitmap[], screen: readonly ImageBitmap[]): PlacementResult;
  /**
   * 기준을 적용합니다. `fitCalibration` 이 방금 낸 값이거나,
   * IndexedDB 에서 되살린 지난 Take 의 값입니다.
   */
  calibrate(ref: ZoneReference): void;
  /**
   * 프레임 하나를 판단합니다. **전처리·얼굴검출·특징추출까지 이 안에서 합니다** —
   * FE 는 프레임만 넘깁니다.
   *
   * 판단할 수 없으면 null — 얼굴이 없거나(AI `face_valid: false`) 캘리브레이션이
   * 없는 경우입니다. 얼굴은 있는데 분류기가 기권했으면 null 이 아니라
   * `zone: 'UNCERTAIN'` 입니다 — 그 프레임은 표본으로 셉니다.
   * null 은 버려지고 TemporalVoter 의 MIN_SAMPLES 규칙이 그 1초를
   * UNCERTAIN 으로 만듭니다.
   *
   * 1초 다수결은 여기서 하지 않습니다. FrameVerdict 주석 참고.
   *
   * ★ 비트맵은 **호출부가 닫습니다.** 이 함수 안에서 close() 하지 마세요.
   */
  classify(frame: ImageBitmap, tMs: Ms): FrameVerdict | null;
  dispose(): void;
  /**
   * Take 에 기록할 버전 문자열. AI 모델 버전 · 백본 · 분류기를 `+` 로 잇습니다
   * (예: `gaze_v1.1.0+head_pose+reference_anchor_v1`, `aiAdapter.ts`).
   * 다수결 규칙(`vote-v1`)은 FE 정책이라 워커가 뒤에 붙입니다.
   */
  readonly version: string;
}

/**
 * 워커 → 메인 메시지. 이것 말고는 넘기지 않습니다.
 *
 * `frameDone`만 프레임 주기이고 나머지는 1초 주기입니다.
 * 원시 좌표는 어느 쪽으로도 나가지 않습니다 — 그게 이 경계의 전부입니다.
 */
export type GazeWorkerOut =
  | { type: 'ready'; version: string }
  /**
   * 프레임 하나를 처리하고 비트맵을 놓았다는 신호. **백프레셔 전용입니다.**
   *
   * 왜 필요한가 — postMessage 는 우체통에 편지를 넣는 것이어서 상대가 읽었는지
   * 알려주지 않습니다. 초당 60장 넣고 24장 처리하면 36장이 쌓이고,
   * 그러면 (1) 프레임마다 GPU 메모리를 잡아 메모리가 차고
   * (2) **보낸 수로 센 fps 가 처리 속도와 갈라져 측정이 거짓이 됩니다.**
   *
   * 메인은 이 신호를 받고서야 다음 프레임을 만듭니다. 그래서 우체통에 항상 1장 이하입니다.
   * decision·perf 는 1초에 하나라 이 역할을 할 수 없습니다.
   *
   * 담는 것은 방금 처리한 프레임의 tMs 하나뿐입니다.
   */
  | { type: 'frameDone'; tMs: Ms }
  /**
   * `fitCalibration` 결과. `ok: false` 면 모델이 없어 재시도해야 합니다.
   * 화면은 기준을 IndexedDB 에 저장하고 다음 Take 에서 되살립니다.
   *
   * `engineVersion` 은 이 기준을 만든 엔진입니다 (`ready` 의 version 과 같은 문자열).
   * 기준과 함께 저장해 두었다가, 되살릴 때 엔진이 바뀌었으면 버립니다 —
   * 다른 모델이 만든 기준을 넣으면 분류기가 그걸 거부할 방법이 없습니다
   * (`calibrate()` 는 반환값이 없습니다). AI 도 스키마가 다른 기준은 거부합니다.
   */
  | { type: 'calibrated'; result: CalibrationResult; engineVersion: string }
  /** `checkPlacement` 결과. 참고용이라 화면은 경고만 합니다 */
  | { type: 'placementChecked'; result: PlacementResult }
  | { type: 'decision'; decision: ZoneDecision }
  | { type: 'perf'; avgFps: number; droppedFrames: number }
  | { type: 'error'; reason: 'ENGINE_UNAVAILABLE' | 'CAMERA_LOST' };

/** 메인 → 워커 메시지. */
export type GazeWorkerIn =
  | { type: 'init' }
  /** 캘리브레이션 4초분. 비트맵은 워커가 닫습니다 */
  | { type: 'fitCalibration'; camera: ImageBitmap[]; bottom: ImageBitmap[] }
  /** 배치 확인 4초분. 비트맵은 워커가 닫습니다 */
  | { type: 'checkPlacement'; camera: ImageBitmap[]; screen: ImageBitmap[] }
  /** 저장해 둔 기준을 되살릴 때 */
  | { type: 'calibrate'; ref: ZoneReference }
  | { type: 'frame'; bitmap: ImageBitmap; tMs: Ms }
  | { type: 'stop' };
