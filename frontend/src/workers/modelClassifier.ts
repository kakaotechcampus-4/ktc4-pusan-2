import type { FrameVerdict, GazeClassifier, ZoneReference } from './gaze.contract';
import type { Ms } from '@/types/api';

/**
 * AI 모듈이 들어올 자리 (A안).
 *
 * ── 왜 이렇게 얇은가 ────────────────────────────────────────────────
 *
 * A안에서 AI팀이 주는 것은 **`GazeClassifier` 를 구현한 JS/TS 모듈**입니다.
 * 전처리·얼굴검출·특징추출·프레임 판정·캘리브레이션 계산이 모두 그 안에 있습니다.
 * 그래서 이 파일이 할 일은 **모듈을 불러와 위임하는 것**뿐입니다.
 *
 * 이전에는 여기에 toTensor / runInference / fromOutput 세 함수가 있었습니다.
 * 그건 FE 가 전처리하고 모델은 추론만 하는 B안(ONNX 파일) 전제였습니다.
 * AI 산출물이 가중치 없는 기하 계산 + 사용자별 분류기라 내보낼 대상이 없어서
 * A안으로 정리했고, 그 세 함수는 사라졌습니다.
 *
 * ── 아직 하지 않은 것 ───────────────────────────────────────────────
 *
 * 모듈이 아직 없습니다. 그래서 `init()` 이 throw 하고,
 * 워커가 그걸 `error { ENGINE_UNAVAILABLE }` 로 바꿉니다.
 * **이 실패 경로가 지금 확인하려는 것입니다** — 모델이 없을 때 앱이 죽지 않는가.
 * `/dev/media` 에서 구현을 model 로 바꾸면 이 경로를 탑니다.
 *
 * ── AI팀에 물어야 채울 수 있는 것 ───────────────────────────────────
 *
 *   1. 모듈 형식 — npm 패키지인가, 파일로 받아 `src/workers/vendor/` 에 두나
 *   2. 초기화에 필요한 자산 — `face_landmarker.task` 같은 파일의 경로·버전
 *   3. 워커에서 도는가 — DOM(`document`·`window`)을 쓰면 워커에서 죽습니다
 *   4. `ZoneReference.model` 에 담기는 값이 구조화 복제 가능한가
 *      (IndexedDB 에 저장해 다음 Take 에서 되살립니다)
 *
 * 3번이 제일 중요합니다. MediaPipe Tasks 는 워커에서 돌지만 초기화 방식이
 * 다르고, 모르고 만들면 나중에 통째로 고칩니다.
 */

/**
 * 모델 자산을 두는 곳. **CDN 에서 받지 않습니다** —
 * 시연장 와이파이가 느리면 발표가 안 됩니다.
 *
 * 지금은 존재 확인에만 씁니다. 어떤 파일이 필요한지는 AI팀 2번 답에 달렸습니다.
 */
const ASSET_DIR = '/models/';

export class ModelGazeClassifier implements GazeClassifier {
  /**
   * 로드 전에는 `unloaded`.
   *
   * 왜 자산 식별자를 쓰나 — `engineVersion` 은 Take 에 영구 고정되고
   * 서버는 시선을 재계산할 수 없습니다. AI팀이 버전 문자열을 주기 전까지는
   * **적어도 "다른 자산이면 다른 값"** 이 되어야 두 Take 를 비교할 때 근거가 됩니다.
   */
  #version = 'gaze-module@unloaded';

  get version(): string {
    return this.#version;
  }

  #ref: ZoneReference | null = null;

  /**
   * AI 모듈 로드. **실패하면 throw 합니다** —
   * 워커가 그걸 받아 `error { ENGINE_UNAVAILABLE }` 로 바꾸고,
   * 화면은 "측정 제외"로 표시하되 타이머·키보드·녹음은 계속 돕니다.
   */
  async init(): Promise<void> {
    // TODO(AI팀 1번) — 모듈이 오면 여기서 import 하고 초기화합니다.
    //   const { createClassifier } = await import('./vendor/gaze');
    //   this.#impl = await createClassifier({ assetDir: ASSET_DIR });
    //
    // 그때까지는 자산 디렉터리에 무엇이 있는지만 확인하고 실패합니다.
    // 이 경로가 도는지가 지금의 관심사입니다.
    const res = await fetch(`${ASSET_DIR}README.md`, { method: 'HEAD' }).catch(() => null);

    throw new Error(
      `gaze module not installed (asset dir ${ASSET_DIR} reachable: ${res?.ok ?? false})`,
    );
  }

  fitCalibration(
    _camera: readonly ImageBitmap[],
    _bottom: readonly ImageBitmap[],
  ): ZoneReference | null {
    // init() 이 throw 하므로 여기까지 오지 않습니다.
    // 모듈이 오면 그대로 위임합니다 — FE 는 기준값을 저장·복원만 합니다.
    return null;
  }

  calibrate(ref: ZoneReference): void {
    this.#ref = ref;
    // TODO(AI팀 4번) — 모듈에 ref.model 을 되돌려줍니다.
  }

  classify(_frame: ImageBitmap, _tMs: Ms): FrameVerdict | null {
    if (!this.#ref) return null;
    // TODO(AI팀 1번) — 모듈에 프레임을 그대로 넘깁니다.
    //   return this.#impl.classify(frame, tMs);
    //
    // ★ 비트맵을 여기서 닫지 마세요. 워커가 finally 에서 닫습니다.
    return null;
  }

  dispose(): void {
    this.#ref = null;
    // TODO(AI팀 1번) — 모듈에 해제할 자원이 있으면 여기서 놓습니다.
  }
}
