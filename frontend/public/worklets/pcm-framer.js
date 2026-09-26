/**
 * 마이크 → 100ms Int16 프레임. 서버로 보내는 오디오 경로의 첫 칸입니다.
 *
 * ── 왜 여기(public/)에 평범한 JS 로 있나 ────────────────────────────
 *
 * `addModule()` 은 실행 시점에 **URL 로** 파일을 받습니다. 이 파일이 모듈 그래프에
 * 들어가면 빌드가 이름에 해시를 붙여 런타임에 못 찾습니다. 같은 폴더의 README 참고.
 *
 * ── 왜 워클릿인가 ──────────────────────────────────────────────────
 *
 * 오디오 스레드에서 돌기 때문에 메인 스레드가 시선 워커·렌더로 막혀 있어도
 * 샘플을 흘리지 않습니다. 발표 중 프레임이 빠지면 그만큼 전사가 사라집니다.
 *
 * ── 메인 스레드로 넘기는 것 ────────────────────────────────────────
 *
 *   pcm        Int16 버퍼 (transfer 로 넘깁니다 — 복사하면 100ms 마다 3.2KB 가 쌓입니다)
 *   startFrame 이 프레임의 첫 샘플이 오디오 시계로 몇 번째인가
 *
 * `startFrame` 을 같이 보내는 이유는 offset_ms 를 **오디오 시계로** 재야 하기
 * 때문입니다. `performance.now()` 로 재면 메인 스레드가 한 번 밀릴 때마다
 * 서버가 보는 시각이 어긋나고, 그 어긋남이 전사 타임스탬프에 그대로 남습니다.
 */

const FRAME_MS = 100;

class PcmFramer extends AudioWorkletProcessor {
  constructor() {
    super();
    // sampleRate 는 워클릿 전역입니다. 16kHz 로 열렸으면 1,600 샘플이 100ms 입니다
    this.size = Math.round((sampleRate * FRAME_MS) / 1000);
    this.buffer = new Int16Array(this.size);
    this.filled = 0;
    this.startFrame = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    // 입력이 아직 없는 렌더 쿼텀입니다. false 를 돌려주면 노드가 영영 멈춥니다
    if (!channel) return true;

    for (let i = 0; i < channel.length; i++) {
      if (this.filled === 0) this.startFrame = currentFrame + i;

      // Int16 으로 스케일합니다. 음수 쪽이 한 칸 더 넓어서 계수가 다릅니다
      const sample = Math.max(-1, Math.min(1, channel[i]));
      this.buffer[this.filled] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      this.filled += 1;

      if (this.filled === this.size) {
        const frame = this.buffer.slice();
        this.port.postMessage({ pcm: frame.buffer, startFrame: this.startFrame }, [frame.buffer]);
        this.filled = 0;
      }
    }

    return true;
  }
}

registerProcessor('pcm-framer', PcmFramer);
