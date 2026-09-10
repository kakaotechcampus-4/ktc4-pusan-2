import type { FrameVerdict, GazeClassifier, GazeSample, ZoneReference } from './gaze.contract';

/**
 * 실모델이 들어올 자리.
 *
 * ── 세 함수로 나눈 이유 ─────────────────────────────────────────────
 *
 *   toTensor(sample)      ← 입력 형식 미정. TODO
 *   runInference(tensor)  ← 지금 비어 있음. 라이브러리가 정해지면 채움
 *   fromOutput(output)    ← 출력 형식 미정. TODO
 *
 * 가운데(파이프라인 배선·실패 처리·버전 기록)는 지금 만들 수 있고,
 * 양쪽 둘만 AI팀 답을 기다립니다. **답이 오면 두 함수만 채우면 됩니다.**
 * 나누지 않고 한 덩이로 두면 답이 왔을 때 이 파일을 다시 설계하게 됩니다.
 *
 * ── 아직 하지 않은 것 ───────────────────────────────────────────────
 *
 * `onnxruntime-web` 을 **설치하지 않았습니다.** AI팀이 실행 형식(ONNX / TFLite)을
 * 확정한 뒤에 넣습니다. 실패 경로(T12)를 시험하는 데는 라이브러리가 필요 없습니다 —
 * 지금 확인하려는 것은 "모델이 없을 때 앱이 죽지 않는가"이고, 그건 init() 만으로 됩니다.
 *
 * ── AI팀에 물어야 채울 수 있는 것 ───────────────────────────────────
 *
 *   1. 실행 형식 — ONNX(onnxruntime-web)인가 TFLite 인가
 *   2. 입력 — 640×480 프레임 그대로인가, 리사이즈해서 줘야 하나. 정규화 규격은
 *   3. 얼굴 검출 포함? 프레임 전체에서 얼굴을 찾는 것까지 모델이 하나
 *   4. 보정 — 사람마다 "정면"이 다른데 모델이 흡수하나, 2지점 보정이 필요하나
 *
 * 4번이 W3 작업(Calibration 화면)을 결정합니다.
 */

/** `public/models/` 에 둡니다. CDN 에서 받지 않습니다 — 시연장 와이파이가 느리면 발표가 안 됩니다. */
const MODEL_URL = '/models/gaze.onnx';

export class ModelGazeClassifier implements GazeClassifier {
  /**
   * 로드 전에는 `unloaded`. init() 이 성공하면 모델 파일의 식별자가 붙습니다.
   *
   * 왜 파일 식별자를 쓰나 — `engineVersion` 은 Take 에 영구 고정되고
   * 서버는 시선을 재계산할 수 없습니다. AI팀이 버전 문자열을 주기 전까지는
   * **적어도 "다른 파일이면 다른 값"** 이 되어야 두 Take 를 비교할 때 근거가 됩니다.
   */
  #version = 'gaze-onnx@unloaded';

  get version(): string {
    return this.#version;
  }

  #ref: ZoneReference | null = null;
  #session: unknown = null;

  /**
   * 모델 가중치 로드. **실패하면 throw 합니다** —
   * 워커가 그걸 받아 `error { ENGINE_UNAVAILABLE }` 로 바꾸고,
   * 화면은 "측정 제외"로 표시하되 타이머·키보드·녹음은 계속 돕니다.
   */
  async init(): Promise<void> {
    const res = await fetch(MODEL_URL, { method: 'HEAD' });

    // dev 서버는 없는 경로에 index.html 을 돌려줄 수 있습니다(SPA 폴백).
    // 200 만 보고 넘어가면 HTML 을 모델로 착각합니다.
    const type = res.headers.get('content-type') ?? '';
    if (!res.ok || type.includes('text/html')) {
      throw new Error(`gaze model not found at ${MODEL_URL} (status ${res.status})`);
    }

    // AI팀이 버전 문자열을 주기 전까지의 임시 식별자.
    // ETag 가 없으면 크기라도 씁니다 — 파일이 바뀌면 값이 바뀌어야 합니다.
    const etag = res.headers.get('etag')?.replaceAll('"', '');
    const size = res.headers.get('content-length');
    this.#version = `gaze-onnx@${etag ?? size ?? 'unknown'}`;

    // TODO(AI팀 1번) — 실행 형식이 정해지면 여기서 세션을 만듭니다.
    //   ONNX 면:  this.#session = await ort.InferenceSession.create(MODEL_URL)
    // 지금은 파일 존재만 확인하고 끝냅니다. 추론은 runInference 가 null 을 냅니다.
    this.#session = null;
  }

  calibrate(ref: ZoneReference): void {
    this.#ref = ref;
  }

  classify(sample: GazeSample): FrameVerdict | null {
    if (!sample.faceFound) return null;

    const tensor = toTensor(sample, this.#ref);
    if (!tensor) return null;

    const output = runInference(tensor, this.#session);
    if (!output) return null;

    return fromOutput(output);
  }

  dispose(): void {
    this.#ref = null;
    this.#session = null;
    // TODO(AI팀 1번) — ONNX 세션은 여기서 release() 해야 합니다.
  }
}

/**
 * 프레임 하나를 모델 입력으로 바꿉니다.
 *
 * TODO(AI팀 2번) — 입력 규격이 정해지면 채웁니다.
 *   · 640×480 을 그대로 받나, 리사이즈해서 줘야 하나
 *   · 정규화 — [0,1] 인가 [-1,1] 인가, 채널 순서는 NCHW 인가 NHWC 인가
 *   · 얼굴 크롭이 필요하면 그 좌표는 누가 주나 (AI팀 3번과 연결)
 *
 * `sample.features` 는 워커가 채워 줍니다. 지금은 빈 배열이라 null 을 냅니다.
 * `ref` 는 모델이 보정을 흡수하지 않는 경우에만 씁니다 (AI팀 4번).
 */
function toTensor(sample: GazeSample, _ref: ZoneReference | null): Float32Array | null {
  if (sample.features.length === 0) return null;
  return sample.features;
}

/**
 * 추론. **지금은 항상 null 입니다** — 라이브러리를 아직 설치하지 않았습니다.
 *
 * TODO(AI팀 1번) — ONNX 면 세션에 feed 하고 결과를 꺼냅니다.
 */
function runInference(_tensor: Float32Array, session: unknown): Float32Array | null {
  if (!session) return null;
  return null;
}

/**
 * 모델 출력을 zone 판정으로 바꿉니다.
 *
 * TODO(AI팀 2번) — 출력 형식이 정해지면 채웁니다.
 *   · zone 3값의 확률인가, 시선 벡터인가
 *   · 벡터면 ZoneReference 와의 거리로 우리가 판정합니다 (dummyClassifier 와 같은 방식)
 *   · 확률이면 argmax 를 쓰고 confidence 는 그 확률을 그대로 씁니다
 *
 * 1초 다수결은 여기서 하지 않습니다 — TemporalVoter 의 일입니다.
 */
function fromOutput(_output: Float32Array): FrameVerdict | null {
  return null;
}
