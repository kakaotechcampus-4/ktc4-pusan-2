import type { FrameVerdict, GazeClassifier, ZoneReference } from './gaze.contract';
import type { GazeZone, Ms } from '@/types/api';

/**
 * AI 모듈이 오기 전까지 쓰는 더미 분류기.
 *
 * 하는 일은 진짜와 같은 모양입니다 — 프레임 하나를 받아 zone 하나를 냅니다.
 * **판정 근거만 가짜입니다.**
 *
 * ── 왜 프레임을 보지 않는가 ─────────────────────────────────────────
 *
 * A안에서 전처리·얼굴검출·특징추출은 분류기 몫입니다. 더미는 그걸 할 수
 * 없으므로 프레임을 무시하고 **시각(tMs)으로 판정을 만듭니다.**
 *
 * 그래도 검증하려는 것은 다 검증됩니다 — 프레임 펌프·백프레셔·1초 다수결·
 * 구간 압축·저장·조립·종료 흐름은 zone 값이 어디서 왔는지 모릅니다.
 *
 * 이전 더미는 항상 `null` 을 내서 판정이 전부 UNCERTAIN 이었습니다.
 * 그러면 구간 압축과 합계 검증을 눈으로 볼 수 없어서, 번갈아 내도록 했습니다.
 *
 * 1초 다수결은 여기 없습니다 — TemporalVoter 가 맡습니다.
 * 그게 계약을 나눈 이유입니다. 다수결이 분류기 안에 있으면
 * 모델을 갈아끼울 때 우리 정책이 같이 사라집니다.
 *
 * 실모듈로 바꿀 때: 생성자만 ModelGazeClassifier 로 갈아끼웁니다.
 * 화면 코드는 한 줄도 안 바뀝니다.
 */

/** 이 주기로 CAMERA ↔ BOTTOM 을 번갈아 냅니다. 1초 판정보다 길어야 구간이 생깁니다 */
const SWITCH_MS = 4000;

/** 전환 직후 이만큼은 UNCERTAIN 을 냅니다 — 실제로도 시선이 옮겨가는 동안은 판정이 흔들립니다 */
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
  ): ZoneReference | null {
    // AI 설정의 min_samples_per_class 와 같은 취지. 모자라면 재시도를 안내합니다.
    if (camera.length < 10 || bottom.length < 10) return null;

    return {
      camera: [0, 0, -1],
      bottom: [0, -0.5, -0.85],
      // AI 설정의 min_separability 가 1.00 입니다. 더미는 통과하는 값을 냅니다.
      separability: 1.4,
      coordinateSpace: 'mirrored',
      layoutSignature: 'dummy',
      model: { kind: 'dummy' },
    };
  }

  calibrate(ref: ZoneReference): void {
    this.ref = ref;
  }

  classify(_frame: ImageBitmap, tMs: Ms): FrameVerdict | null {
    // 캘리브레이션 전에는 판단하지 않습니다. 진짜도 같습니다 —
    // 그 사람의 기준이 없으면 각도만으로는 아무것도 못 정합니다.
    //
    // null 은 버려지고, 그 1초의 표본이 모자라면
    // TemporalVoter 의 MIN_SAMPLES 규칙이 UNCERTAIN 을 냅니다.
    if (!this.ref) return null;

    const phase = tMs % (SWITCH_MS * 2);
    const intoHalf = phase % SWITCH_MS;

    // 전환 직후는 판정 불가로 둡니다.
    if (intoHalf < BLUR_MS) return null;

    const zone: GazeZone = phase < SWITCH_MS ? 'CAMERA' : 'BOTTOM';

    // 전환에서 멀어질수록 확신이 올라갑니다 — 0.6 에서 0.95 까지.
    // 실모듈은 여기에 분류기 확률이 들어옵니다.
    const settled = (intoHalf - BLUR_MS) / (SWITCH_MS - BLUR_MS);
    const confidence = 0.6 + 0.35 * settled;

    return { zone, confidence };
  }

  dispose(): void {
    this.ref = null;
  }
}
