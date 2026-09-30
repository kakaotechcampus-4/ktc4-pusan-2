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
 *
 * ── 두 갈래로 잽니다 ────────────────────────────────────────────────
 *
 *   source ─┬─ analyser            → read()     무음 판정. 가공 없는 RMS
 *           └─ A-가중(2차 × 3) → analyser → levelDb()  목소리 크기. dB(A), Fast 가중
 *
 * 무음 판정을 A-가중 뒤에서 하지 않는 이유 — "샘플이 전부 0" 을 보려는 것이라
 * 필터가 끼면 안 됩니다. 크기는 반대로 귀가 느끼는 쪽에 맞춰야 합니다.
 */

import { aWeightingSections } from './aWeighting';
import { createLoudness, type SpeechLevel } from './loudness';

/** 이 값 미만이면 "소리가 없다"로 봅니다. 완전한 0 만 보면 미세한 노이즈에 속습니다. */
const SILENCE_RMS = 0.005;

export interface LevelMeter {
  /**
   * 0..1 RMS. **매 프레임 불러도 되게** 만들어 두었습니다 — 상태로 올리지 마세요.
   * 부를 때마다 아래 levelDb() 도 같이 갱신됩니다. 그래서 먼저 부릅니다
   */
  read(): number;
  /** 목소리 크기. A-가중 · Fast 가중 dBFS 입니다 (실제 dB 가 아닙니다 — loudness.ts) */
  levelDb(): number;
  /** 말한 구간만 모은 평균 크기. 침묵은 빠집니다 */
  speech(): SpeechLevel;
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

  // 목소리 크기 갈래. 계수는 이 AudioContext 의 샘플레이트로 만들어야 맞습니다
  const filters = aWeightingSections(ctx.sampleRate).map((s) =>
    ctx.createIIRFilter(s.feedforward, s.feedback),
  );
  const weighted = ctx.createAnalyser();
  weighted.fftSize = analyser.fftSize;
  filters.reduce<AudioNode>((prev, f) => prev.connect(f), source).connect(weighted);
  const loudness = createLoudness();

  const buf = new Float32Array(analyser.fftSize);
  const wbuf = new Float32Array(weighted.fftSize);
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

      weighted.getFloatTimeDomainData(wbuf);
      let wsum = 0;
      for (let i = 0; i < wbuf.length; i++) wsum += wbuf[i]! * wbuf[i]!;

      const now = performance.now();
      loudness.update(wsum / wbuf.length, now);

      if (rms < SILENCE_RMS) {
        silentSince ??= now;
      } else {
        silentSince = null;
      }

      return rms;
    },

    levelDb: () => (stopped ? -100 : loudness.levelDb()),

    speech: () => loudness.speech(),

    silentMs() {
      if (silentSince === null) return 0;
      return Math.round(performance.now() - silentSince);
    },

    stop() {
      stopped = true;
      source.disconnect();
      analyser.disconnect();
      filters.forEach((f) => f.disconnect());
      weighted.disconnect();
      ctx.close().catch(() => undefined);
    },
  };
}

export interface MeasureStream {
  stream: MediaStream;
  /** 브라우저가 가공을 정말 껐나. false 면 크기가 자동 음량 조절에 휘둘립니다 */
  unprocessed: boolean;
}

/**
 * 음량을 잴 **가공 없는** 마이크 트랙을 따로 받습니다.
 *
 * ── 왜 따로 받나 ────────────────────────────────────────────────────
 * 녹음·STT 가 쓰는 스트림은 잡음 제거·에코 제거가 켜져 있고, 크롬은 자동 음량
 * 조절(autoGainControl)도 기본으로 켭니다. 자동 음량 조절이 켜져 있으면 작게
 * 말해도 브라우저가 키워서, **작은 목소리가 작게 재지지 않습니다.**
 * 그렇다고 그 스트림의 가공을 끄면 녹음과 STT 품질이 떨어집니다.
 *
 * ── 왜 clone() + applyConstraints() 가 아닌가 ───────────────────────
 * 크롬에서 확인해 보니 복제 트랙에 가공을 끄라고 해도 **오류 없이 무시되고**
 * 원래 설정이 그대로 남았습니다. 같은 장치로 getUserMedia 를 한 번 더 부르면
 * 새 트랙만 가공이 꺼지고 기존 트랙은 그대로였습니다 (2026-09-30, Chrome).
 *
 * 권한은 이미 받은 뒤라 팝업이 다시 뜨지 않습니다. 실패하면 null 이고,
 * 부르는 쪽은 원래 스트림으로 잽니다 — 음량을 못 재는 것보다 낫습니다.
 */
export async function openMeasureStream(stream: MediaStream): Promise<MeasureStream | null> {
  const track = stream.getAudioTracks()[0];
  if (!track) return null;
  const deviceId = track.getSettings().deviceId;

  try {
    const raw = await navigator.mediaDevices.getUserMedia({
      audio: {
        // 녹음 중인 그 마이크여야 합니다. ideal 이면 브라우저가 다른 장치를 열 수 있습니다
        ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
        autoGainControl: false,
        noiseSuppression: false,
        echoCancellation: false,
      },
    });

    // ★ 한 번 더 받으면서 기존 트랙이 끊기는 브라우저가 있으면 녹음·STT 가 죽습니다.
    //   되살릴 방법이 없으니 적어도 드러냅니다 (크롬에서는 끊기지 않았습니다)
    if (track.readyState !== 'live') {
      console.error('[mic] 측정용 트랙을 받다가 녹음용 트랙이 끊겼습니다');
    }

    const settings = raw.getAudioTracks()[0]?.getSettings();
    const unprocessed = settings?.autoGainControl === false;
    if (!unprocessed) {
      console.warn('[mic] 브라우저가 자동 음량 조절을 끄지 않았습니다. 목소리 크기가 부정확합니다');
    }
    return { stream: raw, unprocessed };
  } catch (err: unknown) {
    console.warn('[mic] 측정용 트랙을 받지 못해 녹음용 스트림으로 잽니다', err);
    return null;
  }
}
