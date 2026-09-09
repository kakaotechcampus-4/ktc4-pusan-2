/**
 * 마이크 음량(RMS) 읽기.
 *
 * ── 라이브러리를 안 쓰는 이유 ───────────────────────────────────────
 *
 * *"권한은 살아 있는데 입력만 안 잡히는"* 상황을 잡아야 합니다 —
 * 이어폰을 뽑았을 때, 다른 앱이 마이크를 점유했을 때, 시스템에서 음소거됐을 때.
 * 이때 `getUserMedia` 는 성공하고 트랙도 `live` 인데 **샘플이 전부 0** 입니다.
 *
 * 래퍼를 끼우면 "레벨" 이라는 가공된 숫자만 받게 되어 그 판단을 할 수 없습니다.
 * 그래서 AnalyserNode 에서 직접 읽고, **조용한 시간을 직접 셉니다.**
 *
 * ── 레벨을 React 상태로 올리지 않는 이유 ────────────────────────────
 *
 * 초당 수십 번 바뀝니다. setState 로 올리면 그만큼 렌더가 돌고,
 * 같은 메인 스레드에서 시선 프레임 펌프가 돌고 있어서 fps 가 떨어집니다.
 * 읽는 쪽이 rAF 안에서 read() 를 부르고 DOM 에 직접 씁니다.
 */

/** 이 값 미만이면 "소리가 없다"로 봅니다. 완전한 0 만 보면 미세한 노이즈에 속습니다. */
const SILENCE_RMS = 0.005;

export interface LevelMeter {
  /** 0..1 RMS. **매 프레임 불러도 되게** 만들어 두었습니다 — 상태로 올리지 마세요 */
  read(): number;
  /**
   * 연속으로 조용한 시간. 권한은 있는데 입력이 없는 상황의 근거입니다.
   * 발표 중에 이 값이 계속 커지면 사용자에게 알려야 합니다.
   */
  silentMs(): number;
  /** `suspended` 면 오디오가 흐르지 않습니다 — 아래 createLevelMeter 주석 참고 */
  readonly state: AudioContextState;
  stop(): void;
}

/**
 * ★ **사용자 제스처 안에서 부르세요.**
 *
 * AudioContext 를 제스처 밖에서 만들면 `suspended` 로 시작하고,
 * **에러 없이 오디오가 안 흐릅니다.** 음량 바가 0 에 붙어 있는데 원인 표시가 없습니다.
 *
 * 클릭 핸들러에서 `await getUserMedia()` 를 거친 뒤라면 이미 제스처 밖이지만,
 * 페이지에 한 번이라도 상호작용이 있었으면 resume() 이 통합니다(sticky activation).
 * 그래서 만들자마자 resume() 을 시도하고, 그래도 안 되면 `state` 로 알립니다 —
 * 조용히 실패하지 않게 하는 것이 이 함수의 절반입니다.
 */
export async function createLevelMeter(stream: MediaStream): Promise<LevelMeter | null> {
  if (stream.getAudioTracks().length === 0) return null;

  const ctx = new AudioContext();
  if (ctx.state === 'suspended') {
    // 실패해도 계속 갑니다 — state 로 드러내는 것이 목적입니다.
    await ctx.resume().catch(() => undefined);
  }

  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  // 2048 은 약 43ms 창(48kHz). 말하기 음량에는 이 정도가 적당하고,
  // 더 키우면 반응이 늦고 더 줄이면 숫자가 튑니다.
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.3;
  source.connect(analyser);
  // ★ 스피커로 내보내지 않습니다. destination 에 연결하면 자기 목소리가
  //   스피커로 나가고 하울링이 생깁니다.

  const buf = new Float32Array(analyser.fftSize);
  let silentSince: number | null = performance.now();
  let stopped = false;

  return {
    state: ctx.state,

    read() {
      if (stopped) return 0;
      analyser.getFloatTimeDomainData(buf);

      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i]! * buf[i]!;
      const rms = Math.sqrt(sum / buf.length);

      const now = performance.now();
      if (rms < SILENCE_RMS) {
        silentSince ??= now;
      } else {
        silentSince = null;
      }

      return rms;
    },

    silentMs() {
      if (silentSince === null) return 0;
      return Math.round(performance.now() - silentSince);
    },

    stop() {
      stopped = true;
      source.disconnect();
      analyser.disconnect();
      void ctx.close().catch(() => undefined);
    },
  };
}
