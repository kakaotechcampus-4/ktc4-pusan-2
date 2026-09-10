import type { FrameVerdict, GazeClassifier, GazeSample, ZoneReference } from './gaze.contract';
import type { GazeZone } from '@/types/api';

/**
 * AI 모델이 오기 전까지 쓰는 더미 분류기.
 *
 * 하는 일은 진짜와 같은 모양입니다 — 프레임 하나를 받아 zone 하나를 냅니다.
 * 판정 근거만 가짜입니다(두 기준 벡터와 headPose 의 거리 비교).
 *
 * 1초 다수결은 여기 없습니다 — TemporalVoter 가 맡습니다.
 * 그게 계약을 나눈 이유입니다. 다수결이 분류기 안에 있으면
 * 모델을 갈아끼울 때 우리 정책이 같이 사라집니다.
 *
 * 실모델로 바꿀 때: 생성자만 ModelGazeClassifier 로 갈아끼웁니다.
 * 화면 코드는 한 줄도 안 바뀝니다.
 */
export class DummyGazeClassifier implements GazeClassifier {
  readonly version = 'dummy@heuristic-v0';

  private ref: ZoneReference | null = null;

  async init(): Promise<void> {
    // 실모델은 여기서 가중치를 받습니다. 더미는 할 일이 없습니다.
  }

  calibrate(ref: ZoneReference): void {
    this.ref = ref;
  }

  classify(sample: GazeSample): FrameVerdict | null {
    // 판단할 수 없는 프레임은 null 로 버립니다. 그 1초의 표본이 모자라면
    // TemporalVoter 가 MIN_SAMPLES 규칙으로 UNCERTAIN 을 냅니다.
    if (!sample.faceFound) return null;
    if (!this.ref) return null;
    if (!sample.headPose) return null;

    const dc = dist(sample.headPose, this.ref.camera);
    const db = dist(sample.headPose, this.ref.bottom);
    const zone: GazeZone = dc < db ? 'CAMERA' : 'BOTTOM';

    // 두 기준 중 가까운 쪽에 얼마나 치우쳤나 — 0.5(애매) ~ 1(확실).
    // 실모델은 여기에 소프트맥스 확률이 들어옵니다.
    const sum = dc + db;
    const confidence = sum === 0 ? 0.5 : Math.max(dc, db) / sum;

    return { zone, confidence };
  }

  dispose(): void {
    this.ref = null;
  }
}

function dist(a: readonly [number, number, number], b: readonly [number, number, number]): number {
  const dx = a[0] - b[0];
  const dy = a[1] - b[1];
  const dz = a[2] - b[2];
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}
