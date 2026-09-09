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
 */

import type { GazeZone, Ms } from '@/types/api';

/** 캘리브레이션 기준 벡터. 서버로 보내지 않고 IndexedDB에만 둡니다. */
export interface ZoneReference {
  /** 2점 — 카메라를 볼 때, 화면을 볼 때 */
  camera: readonly [number, number, number];
  bottom: readonly [number, number, number];
  /** 두 기준이 얼마나 떨어져 있나. 낮으면 판정을 믿을 수 없습니다 */
  separability: number;
  /** 미러링된 영상인지 — 좌우가 뒤집히는 고전적인 버그의 원인 */
  coordinateSpace: 'raw' | 'mirrored';
  /** 해상도·배율·카메라 위치. 다르면 다른 기기의 값입니다 */
  layoutSignature: string;
}

/**
 * 한 프레임에서 뽑은 모델 입력. 분류기의 입력.
 *
 * `vector: [number, number, number]` 였는데 넓혔습니다 —
 * 모델 입력이 이미지일 수 있어서 숫자 3개에 담기지 않습니다.
 * 길이를 정하지 않는 이유도 그것입니다. AI팀이 입력 형식을 알려주면 채웁니다.
 */
export interface GazeSample {
  tMs: Ms;
  /**
   * 모델에 그대로 넣는 값. 길이는 모델이 정합니다.
   * 지금은 빈 배열입니다 — 추론이 없어서 채울 것이 없습니다(자리만 만들어 둡니다).
   */
  features: Float32Array;
  /**
   * 고개 방향 (yaw, pitch, roll). 모델이 안 주면 null.
   * CAMERA↔BOTTOM 은 고개도 함께 움직이므로 이것만으로도 축소 판정이 가능합니다 (I-19).
   */
  headPose: readonly [number, number, number] | null;
  /** 얼굴이 잡혔는지. false면 이 프레임은 버립니다 */
  faceFound: boolean;
  confidence: number;
}

/**
 * 프레임 **하나**에 대한 판단. 1초 다수결은 여기서 하지 않습니다.
 *
 * 왜 나눴는가 — 1초 다수결은 모델의 일이 아니라 우리 정책입니다.
 * 분류기 안에 두면 모델을 갈아끼울 때 정책이 같이 사라집니다.
 * 엔진 버전 문자열에 `vote-v1`이 별도 부품으로 적히는 것도 같은 이유입니다.
 * 다수결은 workers/temporalVoter.ts 가 맡습니다.
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
  /** 캘리브레이션 기준 적용 */
  calibrate(ref: ZoneReference): void;
  /**
   * 프레임 하나를 판단합니다. 판단할 수 없으면 null —
   * 얼굴이 없거나 입력이 모자란 경우입니다. null 은 버려지고
   * TemporalVoter 의 MIN_SAMPLES 규칙이 그 1초를 UNCERTAIN 으로 만듭니다.
   *
   * 1초 다수결은 여기서 하지 않습니다. FrameVerdict 주석 참고.
   */
  classify(sample: GazeSample): FrameVerdict | null;
  dispose(): void;
  /** Take에 기록할 버전 문자열. 셋을 다 담습니다 */
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
  | { type: 'decision'; decision: ZoneDecision }
  | { type: 'perf'; avgFps: number; droppedFrames: number }
  | { type: 'error'; reason: 'ENGINE_UNAVAILABLE' | 'CAMERA_LOST' };

/** 메인 → 워커 메시지. */
export type GazeWorkerIn =
  | { type: 'init' }
  | { type: 'calibrate'; ref: ZoneReference }
  | { type: 'frame'; bitmap: ImageBitmap; tMs: Ms }
  | { type: 'stop' };
