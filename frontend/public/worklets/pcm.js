/**
 * 마이크 PCM 을 100ms 씩 모아 메인 스레드로 넘깁니다. **STT 전용 경로입니다.**
 *
 * ── 왜 여기 평범한 JS 로 있나 (CLAUDE.md 7번 · worklets/README.md) ──
 * 번들러를 태우지 않습니다. 모듈 그래프에 들어가면 경로가 해시로 바뀌어 런타임에
 * 못 찾습니다. 게다가 Vite 의 `?worker&url` 로 받으면 dev 에서 파일 맨 앞에
 * `import "/node_modules/vite/dist/client/env.mjs"` 가 **주입**되는데, AudioWorklet 에서
 * ES `import` 가 도는지는 브라우저마다 갈려 `addModule()` 이 통째로 실패할 수 있습니다.
 *
 * 대신 타입 검사를 못 받습니다. 그래서 여기 있는 상수는 pcm.ts 와 **같아야 하고**,
 * 그 사실을 pcm.test.ts 가 이 파일을 읽어서 지킵니다.
 *
 * ── 왜 워크릿인가 ───────────────────────────────────────────────────
 * 오디오는 끊기면 안 됩니다. 메인 스레드에서는 React 렌더와 시선 프레임 펌프가
 * 같이 돕니다. 여기는 오디오 전용 스레드라 메인이 바빠도 정확한 주기로 돕니다.
 *
 * ── 리샘플링은 여기서 하지 않습니다 ─────────────────────────────────
 * `AudioContext({ sampleRate: 16000 })` 로 만들면 브라우저가 에일리어싱 방지까지
 * 포함해 제대로 리샘플링해 줍니다. 3 개 중 1 개만 집는 식으로 직접 줄이면 높은
 * 주파수가 낮은 주파수인 척 되돌아와 잡음이 됩니다. 44.1kHz 마이크는 16kHz 의
 * 정수 배도 아닙니다. 그래서 이 파일이 하는 일은 **모아서 넘기는 것**뿐입니다.
 */

/** pcm.ts 의 SAMPLES_PER_FRAME 과 같아야 합니다 (pcm.test.ts 가 지킵니다) */
const SAMPLES_PER_FRAME = 1600;

class PcmFrameProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    /** 100ms 가 찰 때까지 담아 두는 곳 */
    this.buffer = new Float32Array(SAMPLES_PER_FRAME);
    this.filled = 0;
    /** 이 프레임의 첫 샘플이 스트림 시작에서 몇 번째인가 — 시각을 여기서 만듭니다 */
    this.frameStartSample = currentFrame;
  }

  /**
   * 워크릿은 명세상 **128 샘플씩** 부릅니다 (16kHz 에서 8ms). 그대로 넘기면 초당
   * 125 번이라 오버헤드가 큽니다. 1600 샘플 = 100ms 를 모아 한 번에 넘깁니다.
   */
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    // 입력이 끊겨도 true 를 돌려줍니다 — false 면 노드가 영영 멈춥니다
    if (!channel) return true;

    let read = 0;
    while (read < channel.length) {
      const room = SAMPLES_PER_FRAME - this.filled;
      const take = Math.min(room, channel.length - read);
      this.buffer.set(channel.subarray(read, read + take), this.filled);
      this.filled += take;
      read += take;

      if (this.filled === SAMPLES_PER_FRAME) this.flush();
    }
    return true;
  }

  /**
   * 한 프레임을 메인으로 넘깁니다.
   *
   * ★ 버퍼의 소유권을 넘기고(transfer) 새로 잡습니다. 같은 버퍼를 재사용하면서
   *   넘기면 메인이 읽기 전에 다음 100ms 가 덮어씁니다.
   *
   * 시각은 `currentFrame`(스트림 시작부터의 샘플 수)에서 냅니다. 메인의
   * `performance.now()` 로 찍으면 메인이 바쁠 때 밀린 만큼 그대로 오차가 됩니다.
   */
  flush() {
    const startMs = Math.round((this.frameStartSample / sampleRate) * 1000);
    this.port.postMessage({ pcm: this.buffer, startMs }, [this.buffer.buffer]);

    this.buffer = new Float32Array(SAMPLES_PER_FRAME);
    this.frameStartSample += SAMPLES_PER_FRAME;
    this.filled = 0;
  }
}

registerProcessor('pcm-frame', PcmFrameProcessor);
