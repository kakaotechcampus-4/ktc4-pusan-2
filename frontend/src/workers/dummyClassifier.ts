import type {
  CalibrationResult,
  GazeClassifier,
  GazeFrame,
  PlacementResult,
  ZoneReference,
} from './gaze.contract';
import type { Ms } from '@/types/api';

/**
 * AI 모듈이 오기 전까지 쓰는 더미 분류기.
 *
 * 하는 일은 진짜와 같은 모양입니다 — 프레임 하나를 받아 프레임 근거(`GazeFrame`) 하나를 냅니다.
 * **판정 근거만 가짜입니다.**
 *
 * ── 왜 프레임을 보지 않는가 ─────────────────────────────────────────
 *
 * A안에서 전처리·얼굴검출·특징추출은 분류기 몫입니다. 더미는 그걸 할 수
 * 없으므로 프레임을 무시하고 **시각(tMs)으로 판정을 만듭니다.**
 *
 * 그래도 검증하려는 것은 다 검증됩니다 — 프레임 펌프·백프레셔·1초 묶기·
 * 저장·조립·종료 흐름은 판정이 어디서 왔는지 모릅니다.
 *
 * 이전 더미는 항상 `null` 을 내서 판정이 전부 UNCERTAIN 이었습니다.
 * 그러면 구간과 합계를 눈으로 볼 수 없어서, 번갈아 내도록 했습니다.
 *
 * 1초 묶기는 여기 없습니다 — 워커가 엔진의 `GazeSlicer` 에 넣습니다.
 * 더미도 같은 묶는 도구를 거치므로 1초 기록의 모양은 실모듈과 같습니다.
 *
 * 실모듈로 바꿀 때: 생성자만 ModelGazeClassifier 로 갈아끼웁니다.
 * 화면 코드는 한 줄도 안 바뀝니다.
 */

/** 이 주기로 CAMERA ↔ BOTTOM 을 번갈아 냅니다. 1초 판정보다 길어야 구간이 생깁니다 */
const SWITCH_MS = 4000;

/** 전환 직후 이만큼은 얼굴을 놓친 것으로 냅니다 — 실제로도 시선이 옮겨가는 동안은 판정이 흔들립니다 */
const BLUR_MS = 400;

export class DummyGazeClassifier implements GazeClassifier {
  readonly version = 'dummy@heuristic-v0';

  private ref: ZoneReference | null = null;

  async init(): Promise<void> {
    // 실모듈은 여기서 가중치를 받습니다. 더미는 할 일이 없습니다.
  }

  /**
   * 진짜는 4초분 프레임에서 그 사람의 기준을 학습합니다.
   * 더미는 프레임을 볼 수 없으니 **개수만 확인**하고 가짜 기준을 냅니다.
   *
   * 개수를 보는 이유 — 화면이 4초를 제대로 모아 보냈는지가 이 단계의
   * 유일한 실패 원인이고, 그건 프레임을 안 봐도 알 수 있습니다.
   */
  fitCalibration(
    camera: readonly ImageBitmap[],
    bottom: readonly ImageBitmap[],
  ): CalibrationResult {
    // AI 와 같은 세 갈래입니다 —
    //   한쪽이 0장이면 모델을 못 만듭니다(막음)
    //   min_samples_per_class(10) 미만이면 모델은 있지만 품질 미달(POOR, 진행 가능)
    if (camera.length === 0 || bottom.length === 0) {
      return { ok: false, reason: 'NOT_ENOUGH_SAMPLES' };
    }
    const enough = camera.length >= 10 && bottom.length >= 10;

    return {
      ok: true,
      ref: {
        quality: enough ? 'GOOD' : 'POOR',
        // 프레임을 안 봤으니 수치를 모릅니다. 0 이 아니라 null 입니다
        metrics: null,
        model: { kind: 'dummy' },
      },
      advice: enough ? null : 'NOT_ENOUGH_SAMPLES',
    };
  }

  /**
   * 진짜는 두 응시의 각도 차이로 배치를 추정합니다.
   * 더미는 프레임을 볼 수 없으니 **개수만 보고** 늘 TOP 이라고 답합니다 —
   * 화면 흐름(경고 없이 캘리브레이션으로 넘어가는 길)을 확인하는 용도입니다.
   */
  checkPlacement(camera: readonly ImageBitmap[], screen: readonly ImageBitmap[]): PlacementResult {
    // AI 설정의 min_samples_per_target 과 같은 값, 같은 사유입니다
    if (camera.length < 8 || screen.length < 8) {
      return { placement: 'INCONCLUSIVE', supported: false, reason: 'NOT_ENOUGH_SAMPLES' };
    }
    return { placement: 'TOP', supported: true, reason: 'OK' };
  }

  calibrate(ref: ZoneReference): void {
    this.ref = ref;
  }

  classify(_frame: ImageBitmap, tMs: Ms): GazeFrame | null {
    // 캘리브레이션 전에는 판단하지 않습니다. 진짜도 같습니다 —
    // 그 사람의 기준이 없으면 각도만으로는 아무것도 못 정합니다.
    if (!this.ref) return null;

    const phase = tMs % (SWITCH_MS * 2);
    const intoHalf = phase % SWITCH_MS;

    // 촬영 조건은 모르니 믿을 만하다고 둡니다 (엔진도 조건을 모르면 1 입니다)
    const frame = { t_ms: tMs, direction: null, reliability: 1, issues: [] };

    // 전환 직후는 얼굴을 놓친 프레임으로 둡니다. 그 1초에 얼굴 있는 프레임이 모자라면
    // 엔진의 묶는 도구가 UNMEASURED 로 셉니다
    if (intoHalf < BLUR_MS) return { ...frame, state: 'UNMEASURED' };
    return { ...frame, state: phase < SWITCH_MS ? 'CAMERA' : 'BOTTOM' };
  }

  dispose(): void {
    this.ref = null;
  }
}
