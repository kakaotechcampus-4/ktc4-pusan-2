import type {
  CalibrationResult,
  FrameVerdict,
  GazeClassifier,
  PlacementResult,
  ZoneReference,
} from './gaze.contract';
import type { Ms } from '@/types/api';
import type { GazeEngine } from '@/vendor/gaze/engine';
import { engineVersion, toCalibrationResult, toFrameVerdict, toPlacementResult } from './aiAdapter';

/**
 * AI 시선 엔진 v1.1 (A안).
 *
 * ── 왜 이렇게 얇은가 ────────────────────────────────────────────────
 *
 * A안에서 AI팀이 주는 것은 **`GazeClassifier` 를 구현한 JS/TS 모듈**입니다.
 * 전처리·얼굴검출·고개 방향·프레임 판정·캘리브레이션 계산이 모두 그 안에 있습니다.
 * 그래서 이 파일이 할 일은 **모듈을 불러와 위임하는 것**뿐입니다.
 * 모양은 AI 쪽 `test/fe-contract/conformance.ts` 와 같습니다 — AI 가 이 파일이 될 코드를
 * 우리 `aiAdapter.ts` 에 붙여 미리 돌려 봤습니다.
 *
 * 엔진은 `src/vendor/gaze/engine` 에 복사해 두었습니다 (고치지 않습니다, 그 폴더 README 참고).
 * 동적 import 라 dummy 로 뜬 워커에는 MediaPipe 가 실리지 않습니다.
 *
 * ── AI팀에 물었던 넷의 답 ───────────────────────────────────────────
 *
 *   1. 모듈 형식      — 폴더 복사 (`vendor/gaze/engine`), 진입점 `createClassifier`
 *   2. 초기화 자산    — `/models/` 의 `face_landmarker.task` + `vision_wasm_module_internal.*`
 *   3. 워커에서 도나  — 돕니다. DOM 없이 OffscreenCanvas 로 밝기를 잽니다
 *   4. `model` 복제   — 순수 데이터라 structuredClone · IndexedDB 에 그대로 들어갑니다.
 *                        다른 엔진·설정의 값이면 `calibrate` 가 거절합니다
 *
 * ── 엔진이 내지만 여기서 버리는 것 ──────────────────────────────────
 *
 * v1.1 은 4분류(CAMERA · SCREEN · BOTTOM · OTHER)와 OTHER 의 방향, 촬영 조건(신뢰도)을
 * 함께 냅니다. 계약은 3구역이라(CLAUDE.md 2번) 엔진이 직접 접어서 냅니다 —
 * SCREEN·BOTTOM → BOTTOM, OTHER → UNCERTAIN (AI 기본값, `otherAs`).
 * AI 의 `TemporalSmoother` 출력(`GAZE_STATE`)은 받지 않습니다 — 다수결은 FE 몫입니다.
 */

/**
 * 모델 자산을 두는 곳. **CDN 에서 받지 않습니다** —
 * 시연장 와이파이가 느리면 발표가 안 됩니다.
 */
const ASSET_DIR = '/models/';

export class ModelGazeClassifier implements GazeClassifier {
  /**
   * 로드 전에는 `unloaded`. 로드되면 `gaze_v1.1.0+head_pose+reference_anchor_v1` 입니다.
   *
   * `engineVersion` 은 Take 에 영구 고정되고 서버는 시선을 재계산할 수 없습니다 —
   * 두 Take 를 비교할 때 어느 엔진이 낸 숫자인지가 유일한 근거입니다.
   */
  #version = 'gaze-module@unloaded';

  get version(): string {
    return this.#version;
  }

  #impl: GazeEngine<ImageBitmap> | null = null;
  #ref: ZoneReference | null = null;

  /**
   * AI 모듈 로드. **실패하면 throw 합니다** —
   * 워커가 그걸 받아 `error { ENGINE_UNAVAILABLE }` 로 바꾸고,
   * 화면은 "측정 제외"로 표시하되 타이머·키보드·음성 전송은 계속 돕니다.
   * `/models/` 에 자산이 없을 때가 대표적입니다.
   */
  async init(): Promise<void> {
    const { createClassifier } = await import('@/vendor/gaze/engine');
    this.#impl = await createClassifier({ assetDir: ASSET_DIR });
    this.#version = engineVersion(this.#impl.version);
  }

  fitCalibration(
    camera: readonly ImageBitmap[],
    bottom: readonly ImageBitmap[],
  ): CalibrationResult {
    if (!this.#impl) return { ok: false, reason: 'ENGINE_ERROR' };
    const { quality, model } = this.#impl.fitCalibration(camera, bottom);
    return toCalibrationResult(quality, model);
  }

  checkPlacement(camera: readonly ImageBitmap[], screen: readonly ImageBitmap[]): PlacementResult {
    if (!this.#impl) return { placement: 'INCONCLUSIVE', supported: false, reason: 'ENGINE_ERROR' };
    return toPlacementResult(this.#impl.checkPlacement(camera, screen));
  }

  calibrate(ref: ZoneReference): void {
    this.#ref = ref;
    // 다른 엔진·설정의 기준이면 false 입니다. 그때 #ref 를 남기면 기준 없는 엔진에
    // 프레임이 들어가므로 비웁니다 — 판정이 없으면 다수결이 '측정 못 함'으로 셉니다
    if (this.#impl && !this.#impl.calibrate(ref.model)) this.#ref = null;
  }

  classify(frame: ImageBitmap, tMs: Ms): FrameVerdict | null {
    if (!this.#ref || !this.#impl) return null;
    // ★ 비트맵을 여기서 닫지 마세요. 워커가 finally 에서 닫습니다 (엔진도 닫지 않습니다)
    return toFrameVerdict(this.#impl.classify(frame, tMs));
  }

  dispose(): void {
    this.#ref = null;
    this.#impl?.dispose();
    this.#impl = null;
  }
}
