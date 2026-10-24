import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FRAME_SAMPLES } from './sttProtocol';
import { SttSocket, stopTakeStream, type SocketLike } from './sttSocket';

/** 서버 흉내. 테스트가 직접 열고·보내고·닫습니다 */
class FakeSocket implements SocketLike {
  sent: (string | ArrayBufferView)[] = [];
  closedWith: number | undefined;

  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number; reason?: string }) => void) | null = null;
  onerror: (() => void) | null = null;

  send(data: string | ArrayBufferView) {
    this.sent.push(data);
  }

  close(code?: number) {
    this.closedWith = code;
  }

  open() {
    this.onopen?.();
  }

  emit(message: object) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  serverClose(code: number) {
    this.onclose?.({ code });
  }

  get frames(): DataView[] {
    return this.sent
      .filter((d): d is Uint8Array => typeof d !== 'string')
      .map((d) => new DataView(d.buffer, d.byteOffset, d.byteLength));
  }
}

const READY = { type: 'ready', take_id: 't1', stt_session_no: 0, stt_state: 'ok' };

function status(state: string) {
  return {
    type: 'stt_status',
    state,
    stt_session_no: 0,
    frames: 0,
    dropped_frames: 0,
    silence_ms: 0,
    lost_ms: 0,
  };
}

function pcm(): ArrayBuffer {
  return new Int16Array(FRAME_SAMPLES).buffer;
}

/** connect() 안의 await(토큰) 를 통과시킵니다 */
async function settle() {
  await Promise.resolve();
  await Promise.resolve();
}

let sockets: FakeSocket[] = [];
let states: string[] = [];

function makeSocket(overrides: Partial<ConstructorParameters<typeof SttSocket>[0]> = {}) {
  return new SttSocket({
    takeId: 't1',
    onTranscript: () => undefined,
    onState: (state) => states.push(state),
    getToken: () => Promise.resolve('access-token'),
    // 느린 재시도 간격을 정확히 5초로 고정합니다. jitter 는 따로 확인합니다
    random: () => 0.5,
    createSocket: () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    },
    ...overrides,
  });
}

beforeEach(() => {
  sockets = [];
  states = [];
});

afterEach(() => vi.useRealTimers());

describe('연결과 인증', () => {
  it('첫 메시지로 auth 를 보내고, ready 전 오디오는 버퍼에 쌓았다가 순서대로 흘린다', async () => {
    const stt = makeSocket();
    stt.start();
    await settle();

    const socket = sockets[0]!;
    socket.open();
    expect(JSON.parse(socket.sent[0] as string)).toEqual({
      type: 'auth',
      token: 'access-token',
    });

    // ready 전 — 아직 아무것도 나가지 않습니다
    stt.sendFrame(pcm(), 0);
    stt.sendFrame(pcm(), 100);
    expect(socket.frames).toHaveLength(0);
    expect(stt.bufferedFrames).toBe(2);

    socket.emit(READY);
    expect(socket.frames.map((f) => f.getUint32(0, true))).toEqual([1, 2]);
    expect(socket.frames.map((f) => f.getUint32(4, true))).toEqual([0, 100]);

    // ready 뒤에는 바로 나갑니다
    stt.sendFrame(pcm(), 200);
    expect(socket.frames).toHaveLength(3);
    expect(socket.frames[2]!.getUint32(0, true)).toBe(3);
  });

  it('버퍼는 2초(20장)까지만 들고, 넘치면 오래된 것부터 버린다', async () => {
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();

    for (let i = 0; i < 25; i++) stt.sendFrame(pcm(), i * 100);
    expect(stt.bufferedFrames).toBe(20);

    sockets[0]!.emit(READY);
    // 버린 5장만큼 seq 가 건너뜁니다 — 서버는 갭을 무음으로 채웁니다
    expect(sockets[0]!.frames[0]!.getUint32(0, true)).toBe(6);
  });
});

describe('재연결', () => {
  it('1008 로 닫히면 다시 붙지 않는다 — 같은 이유로 또 닫힌다', async () => {
    const onGiveUp = vi.fn();
    const stt = makeSocket({ onGiveUp });
    stt.start();
    await settle();

    sockets[0]!.open();
    sockets[0]!.emit(READY);
    sockets[0]!.serverClose(1008);
    await settle();

    expect(sockets).toHaveLength(1);
    expect(onGiveUp).toHaveBeenCalledWith('FATAL');
    expect(states.at(-1)).toBe('degraded');
  });

  it('비정상 종료는 백오프로 다시 붙고 seq 가 이어진다', async () => {
    vi.useFakeTimers();
    const stt = makeSocket();
    stt.start();
    await settle();

    sockets[0]!.open();
    sockets[0]!.emit(READY);
    stt.sendFrame(pcm(), 0);
    sockets[0]!.serverClose(1006);

    expect(states.at(-1)).toBe('reconnecting');

    await vi.advanceTimersByTimeAsync(500);
    expect(sockets).toHaveLength(2);

    // 끊긴 동안 쌓인 프레임은 ready 뒤에 나갑니다
    stt.sendFrame(pcm(), 100);
    sockets[1]!.open();
    sockets[1]!.emit(READY);

    // 1번은 첫 연결에서 나갔으니 이어서 2번입니다 — 리셋하면 서버가 역행으로 버립니다
    expect(sockets[1]!.frames.map((f) => f.getUint32(0, true))).toEqual([2]);
  });

  it('세 번 실패해도 포기하지 않고 5초마다 다시 붙는다', async () => {
    vi.useFakeTimers();
    const onGiveUp = vi.fn();
    const stt = makeSocket({ onGiveUp });
    stt.start();
    await settle();

    for (let attempt = 0; attempt < 3; attempt++) {
      sockets.at(-1)!.open();
      sockets.at(-1)!.serverClose(1006);
      await vi.advanceTimersByTimeAsync(2_000);
    }
    sockets.at(-1)!.serverClose(1006);
    await settle();

    expect(sockets).toHaveLength(4);
    expect(onGiveUp).not.toHaveBeenCalled();
    expect(states.at(-1)).toBe('degraded');

    await vi.advanceTimersByTimeAsync(4_999);
    expect(sockets).toHaveLength(4);
    await vi.advanceTimersByTimeAsync(1);
    expect(sockets).toHaveLength(5);
    // 느린 재시도 중에는 문구를 바꾸지 않습니다 — 5초마다 깜빡이면 발표자가 신경 씁니다
    expect(states.at(-1)).toBe('degraded');

    sockets.at(-1)!.serverClose(1006);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(sockets).toHaveLength(6);
    expect(onGiveUp).not.toHaveBeenCalled();
  });

  it('느린 재시도 끝에 붙으면 seq 를 이어 보내고 다시 빠른 백오프로 돌아간다', async () => {
    vi.useFakeTimers();
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);
    stt.sendFrame(pcm(), 0);

    // 빠른 백오프 3회를 다 쓰고 느린 재시도로 넘어갑니다
    sockets.at(-1)!.serverClose(1006);
    for (const delay of [500, 1_000, 2_000]) {
      await vi.advanceTimersByTimeAsync(delay);
      sockets.at(-1)!.serverClose(1006);
    }
    await vi.advanceTimersByTimeAsync(5_000);

    stt.sendFrame(pcm(), 100);
    sockets.at(-1)!.open();
    sockets.at(-1)!.emit(READY);
    expect(states.at(-1)).toBe('ok');
    expect(sockets.at(-1)!.frames.map((f) => f.getUint32(0, true))).toEqual([2]);

    // 붙은 뒤 다시 끊기면 처음부터 빠르게 붙어 봅니다
    const before = sockets.length;
    sockets.at(-1)!.serverClose(1006);
    expect(states.at(-1)).toBe('reconnecting');
    await vi.advanceTimersByTimeAsync(500);
    expect(sockets).toHaveLength(before + 1);
  });
});

describe('느린 재시도 간격과 상한', () => {
  /** 빠른 백오프 3회를 다 쓰고 느린 재시도로 넘어간 상태를 만듭니다 */
  async function exhaustFastBackoff() {
    sockets.at(-1)!.serverClose(1006);
    for (const delay of [500, 1_000, 2_000]) {
      await vi.advanceTimersByTimeAsync(delay);
      sockets.at(-1)!.serverClose(1006);
    }
  }

  /** 느린 재시도를 계속 실패시키며 시간을 흘립니다 */
  async function keepFailing(forMs: number) {
    const until = Date.now() + forMs;
    while (Date.now() < until) {
      await vi.advanceTimersByTimeAsync(5_000);
      sockets.at(-1)!.serverClose(1006);
    }
  }

  it.each([
    [0, 4_000],
    [0.9995, 5_999],
  ])('간격은 4~6초 사이에서 무작위로 고른다 (random=%s → %sms)', async (random, delay) => {
    vi.useFakeTimers();
    const stt = makeSocket({ random: () => random });
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);
    await exhaustFastBackoff();
    const before = sockets.length;

    await vi.advanceTimersByTimeAsync(delay - 1);
    expect(sockets).toHaveLength(before);
    await vi.advanceTimersByTimeAsync(1);
    expect(sockets).toHaveLength(before + 1);
  });

  it('끊긴 지 30분이 지나면 멈춘다 — 열어 두고 떠난 탭이 계속 두드리지 않게', async () => {
    vi.useFakeTimers();
    const onGiveUp = vi.fn();
    const stt = makeSocket({ onGiveUp });
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    await exhaustFastBackoff();
    await keepFailing(29 * 60_000);
    expect(onGiveUp).not.toHaveBeenCalled();

    await keepFailing(60_000);
    expect(onGiveUp).toHaveBeenCalledWith('RETRY_LIMIT');
    expect(states.at(-1)).toBe('degraded');

    const after = sockets.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(sockets).toHaveLength(after);
  });

  it('중간에 한 번 붙으면 30분을 처음부터 다시 센다', async () => {
    vi.useFakeTimers();
    const onGiveUp = vi.fn();
    const stt = makeSocket({ onGiveUp });
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    await exhaustFastBackoff();
    await keepFailing(25 * 60_000);
    await vi.advanceTimersByTimeAsync(5_000);
    sockets.at(-1)!.open();
    sockets.at(-1)!.emit(READY);

    // 처음 끊긴 때부터는 30분이 넘지만, 다시 끊긴 때부터는 아직 25분입니다
    await exhaustFastBackoff();
    await keepFailing(25 * 60_000);
    expect(onGiveUp).not.toHaveBeenCalled();
  });
});

describe('붙지 못하는 경우', () => {
  it('소켓을 만들다 던지면 조용히 사라지지 않고 포기를 알린다', async () => {
    const onGiveUp = vi.fn();
    const stt = makeSocket({
      onGiveUp,
      // https 페이지에서 ws:// 를 열 때처럼 생성 자체가 던지는 경우입니다
      createSocket: () => {
        throw new Error('SecurityError');
      },
    });

    stt.start();
    await settle();

    expect(onGiveUp).toHaveBeenCalledWith('CONNECT_FAILED');
    expect(states.at(-1)).toBe('degraded');
  });

  it('토큰을 못 받으면 붙지 않고 알린다', async () => {
    const onGiveUp = vi.fn();
    const stt = makeSocket({ onGiveUp, getToken: () => Promise.resolve(null) });

    stt.start();
    await settle();

    expect(sockets).toHaveLength(0);
    expect(onGiveUp).toHaveBeenCalledWith('NO_TOKEN');
  });
});

describe('토큰 만료', () => {
  it('UNAUTHORIZED 를 받으면 토큰을 새로 받아 한 번 더 붙는다', async () => {
    const asked: boolean[] = [];
    const onGiveUp = vi.fn();
    const stt = makeSocket({
      onGiveUp,
      getToken: ({ renew }) => {
        asked.push(renew);
        return Promise.resolve(renew ? 'fresh-token' : 'stale-token');
      },
    });

    stt.start();
    await settle();
    sockets[0]!.open();

    // 15분짜리 access 토큰이 발표 도중 만료된 상황입니다
    sockets[0]!.emit({ type: 'error', code: 'UNAUTHORIZED', message: '인증이 필요합니다.' });
    sockets[0]!.serverClose(1008);
    await settle();

    expect(asked).toEqual([false, true]);
    expect(sockets).toHaveLength(2);

    sockets[1]!.open();
    expect(JSON.parse(sockets[1]!.sent[0] as string)).toEqual({
      type: 'auth',
      token: 'fresh-token',
    });
    expect(onGiveUp).not.toHaveBeenCalled();
  });

  it('새 토큰으로도 거절당하면 그때 포기한다 — 진짜 인증 실패다', async () => {
    const onGiveUp = vi.fn();
    const stt = makeSocket({ onGiveUp, getToken: () => Promise.resolve('token') });

    stt.start();
    await settle();

    for (const attempt of [0, 1]) {
      sockets[attempt]!.open();
      sockets[attempt]!.emit({ type: 'error', code: 'UNAUTHORIZED', message: '인증 실패' });
      sockets[attempt]!.serverClose(1008);
      await settle();
    }

    expect(sockets).toHaveLength(2);
    expect(onGiveUp).toHaveBeenCalledWith('FATAL');
  });
});

describe('1초 측정값 (metrics)', () => {
  const metrics = (t_ms: number) =>
    ({ type: 'metrics', t_ms, gaze: null, voice: null, slide_number: 1 }) as const;

  /** 보낸 JSON 중 metrics 의 t_ms 만 */
  const sentMetrics = (socket: FakeSocket) =>
    socket.sent
      .filter((d): d is string => typeof d === 'string')
      .map((d) => JSON.parse(d) as { type: string; t_ms?: number })
      .filter((m) => m.type === 'metrics')
      .map((m) => m.t_ms);

  it('ready 뒤에는 바로 보낸다', async () => {
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    stt.sendMetrics(metrics(1_000));
    expect(sentMetrics(sockets[0]!)).toEqual([1_000]);
  });

  it('몰아 보내는 것에만 buffered 를 붙인다 — BE 가 지난 시각으로 코치를 부르지 않게', async () => {
    vi.useFakeTimers();
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    /** 보낸 metrics 의 [t_ms, buffered 칸이 있나 · 값] */
    const flags = (socket: FakeSocket) =>
      socket.sent
        .filter((d): d is string => typeof d === 'string')
        .map((d) => JSON.parse(d) as { type: string; t_ms?: number; buffered?: unknown })
        .filter((m) => m.type === 'metrics')
        .map((m) => [m.t_ms, 'buffered' in m ? m.buffered : 'none'] as const);

    stt.sendMetrics(metrics(1_000));
    sockets[0]!.serverClose(1006);
    stt.sendMetrics(metrics(2_000));
    stt.sendMetrics(metrics(3_000));

    await vi.advanceTimersByTimeAsync(500);
    sockets[1]!.open();
    sockets[1]!.emit(READY);
    stt.sendMetrics(metrics(4_000));

    // 바로 보낸 것에는 칸이 없습니다(없으면 false — architecture.md 1-4)
    expect(flags(sockets[0]!)).toEqual([[1_000, 'none']]);
    expect(flags(sockets[1]!)).toEqual([
      [2_000, true],
      [3_000, true],
      [4_000, 'none'],
    ]);
  });

  it('끊긴 동안 쌓았다가 다시 붙으면 오디오 다음에 보낸다 — 서버로 가는 길이 이것뿐이다', async () => {
    vi.useFakeTimers();
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    sockets[0]!.serverClose(1006);
    stt.sendFrame(pcm(), 0);
    stt.sendMetrics(metrics(1_000));
    stt.sendMetrics(metrics(2_000));
    expect(stt.bufferedMetrics).toBe(2);

    await vi.advanceTimersByTimeAsync(500);
    sockets[1]!.open();
    sockets[1]!.emit(READY);

    expect(sentMetrics(sockets[1]!)).toEqual([1_000, 2_000]);
    // 오디오가 먼저입니다 — 인증(auth) 뒤 첫 전송이 프레임입니다
    expect(typeof sockets[1]!.sent[1]).not.toBe('string');
    expect(stt.bufferedMetrics).toBe(0);
  });

  it('너무 오래 끊기면 오래된 것부터 버린다', async () => {
    const stt = makeSocket();
    stt.start();
    await settle();

    for (let i = 1; i <= 301; i++) stt.sendMetrics(metrics(i * 1_000));
    expect(stt.bufferedMetrics).toBe(300);

    sockets[0]!.open();
    sockets[0]!.emit(READY);
    expect(sentMetrics(sockets[0]!)[0]).toBe(2_000);
  });

  it('끊긴 채로 끝내면 재연결을 기다렸다가 마지막 조각을 stop 앞에 보낸다', async () => {
    vi.useFakeTimers();
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    sockets[0]!.serverClose(1006);
    stt.sendMetrics(metrics(3_400));
    void stt.stop();

    await vi.advanceTimersByTimeAsync(500);
    sockets[1]!.open();
    sockets[1]!.emit(READY);

    expect(sentMetrics(sockets[1]!)).toEqual([3_400]);
    expect(JSON.parse(sockets[1]!.sent.at(-1) as string)).toEqual({ type: 'stop' });
  });

  describe('새로고침 (handOffMetrics · restoredMetrics)', () => {
    const parsed = (data: readonly string[]) =>
      data.map((d) => JSON.parse(d) as { t_ms: number; buffered?: boolean });

    it('끊겨 있으면 쌓아 둔 것과 방금 받은 것을 모두 넘긴다', async () => {
      vi.useFakeTimers();
      const stt = makeSocket();
      stt.start();
      await settle();
      sockets[0]!.open();
      sockets[0]!.emit(READY);
      sockets[0]!.serverClose(1006);
      stt.sendMetrics(metrics(57_000));

      const handed = parsed(stt.handOffMetrics([metrics(58_000), metrics(59_000)]));
      expect(handed.map((m) => [m.t_ms, m.buffered])).toEqual([
        [57_000, true],
        [58_000, true],
        [59_000, true],
      ]);
      // 페이지가 되살아나면 다시 붙어 보냅니다
      expect(stt.bufferedMetrics).toBe(3);
    });

    it('붙어 있으면 바로 보내 보고, 닿았다고 볼 수 없으니 그것도 넘긴다', async () => {
      const stt = makeSocket();
      stt.start();
      await settle();
      sockets[0]!.open();
      sockets[0]!.emit(READY);

      const handed = parsed(stt.handOffMetrics([metrics(59_000)]));
      expect(sentMetrics(sockets[0]!)).toEqual([59_000]);
      expect(handed.map((m) => [m.t_ms, m.buffered])).toEqual([[59_000, true]]);
      expect(stt.bufferedMetrics).toBe(0);
    });

    it('새 페이지의 소켓은 넘겨받은 것을 붙자마자 먼저 보낸다', async () => {
      const handed = makeSocket().handOffMetrics([metrics(58_000), metrics(59_000)]);
      const stt = makeSocket({ restoredMetrics: handed });
      stt.start();
      await settle();
      sockets[0]!.open();
      sockets[0]!.emit(READY);
      stt.sendMetrics(metrics(61_000));

      expect(sentMetrics(sockets[0]!)).toEqual([58_000, 59_000, 61_000]);
      const flags = sockets[0]!.sent
        .filter((d): d is string => typeof d === 'string')
        .map((d) => JSON.parse(d) as { type: string; buffered?: boolean })
        .filter((m) => m.type === 'metrics')
        .map((m) => m.buffered ?? false);
      expect(flags).toEqual([true, true, false]);
    });

    it('넘겨받은 것도 상한(5분)을 넘으면 오래된 것부터 버린다', () => {
      const restored = Array.from({ length: 301 }, (_, i) =>
        JSON.stringify({ ...metrics((i + 1) * 1_000), buffered: true }),
      );
      expect(makeSocket({ restoredMetrics: restored }).bufferedMetrics).toBe(300);
    });

    it('끝내기(stop) 뒤에는 넘길 것이 없다', async () => {
      const stt = makeSocket();
      stt.start();
      await settle();
      sockets[0]!.open();
      sockets[0]!.emit(READY);
      stt.stop().catch(() => undefined);
      expect(stt.handOffMetrics([metrics(9_000)])).toEqual([]);
    });
  });

  it('stop 을 보낸 뒤에는 보내지 않는다', async () => {
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    void stt.stop();
    stt.sendMetrics(metrics(9_000));
    expect(sentMetrics(sockets[0]!)).toEqual([]);
  });
});

describe('종료', () => {
  it('stop 을 보내고 closed 를 받을 때까지 기다린다', async () => {
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    let done = false;
    const stopped = stt.stop().then(() => {
      done = true;
    });

    expect(JSON.parse(sockets[0]!.sent.at(-1) as string)).toEqual({ type: 'stop' });
    await settle();
    // 아직입니다 — 여기서 닫으면 마지막 1~2초 전사가 사라집니다
    expect(done).toBe(false);

    sockets[0]!.emit(status('closed'));
    await stopped;
    expect(done).toBe(true);
    expect(sockets[0]!.closedWith).toBe(1000);
  });

  it('ready 전에 끝내면 버퍼를 먼저 흘리고 stop 을 보낸다', async () => {
    const stt = makeSocket();
    stt.start();
    await settle();

    const socket = sockets[0]!;
    socket.open();
    stt.sendFrame(pcm(), 0);
    stt.sendFrame(pcm(), 100);

    const stopped = stt.stop();

    // 인증 전 오디오는 서버가 안 받습니다. 여기서 stop 부터 보내면 2초가 버려집니다
    expect(socket.sent).toHaveLength(1);

    socket.emit(READY);
    expect(socket.frames.map((f) => f.getUint32(0, true))).toEqual([1, 2]);
    expect(JSON.parse(socket.sent.at(-1) as string)).toEqual({ type: 'stop' });

    socket.emit(status('closed'));
    await expect(stopped).resolves.toBeUndefined();
  });

  it('closed 를 기다리는 중에 소켓이 죽어도 종료가 풀린다', async () => {
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    const stopped = stt.stop();
    // 서버가 정리 도중 끊긴 경우입니다. 여기서 안 풀리면 종료 화면이 3초 멈춰 있습니다
    sockets[0]!.serverClose(1006);

    await expect(stopped).resolves.toBeUndefined();
    expect(sockets).toHaveLength(1);
  });

  it('끊긴 채로 끝내면 재연결을 기다렸다가 남은 음성을 보낸다', async () => {
    vi.useFakeTimers();
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    // 연결이 끊긴 사이에 말한 2초가 버퍼에 남습니다 — 이게 마무리 문장입니다
    sockets[0]!.serverClose(1006);
    stt.sendFrame(pcm(), 0);
    stt.sendFrame(pcm(), 100);
    expect(stt.bufferedFrames).toBe(2);

    let done = false;
    stt.stop().then(() => {
      done = true;
    });

    // 여기서 바로 정리하면 버퍼가 그대로 버려집니다
    await settle();
    expect(done).toBe(false);

    await vi.advanceTimersByTimeAsync(500);
    sockets[1]!.open();
    sockets[1]!.emit(READY);

    expect(sockets[1]!.frames).toHaveLength(2);
    expect(JSON.parse(sockets[1]!.sent.at(-1) as string)).toEqual({ type: 'stop' });
  });

  it('재연결마저 실패하면 더 기다리지 않고 정리한다', async () => {
    vi.useFakeTimers();
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    sockets[0]!.serverClose(1006);
    stt.sendFrame(pcm(), 0);

    const stopped = stt.stop();
    await vi.advanceTimersByTimeAsync(500);
    sockets[1]!.serverClose(1006);

    // 상한(3초)을 다 쓰지 않고 바로 풀립니다
    await expect(stopped).resolves.toBeUndefined();
  });

  it('closed 가 안 오면 3초 뒤에 끊는다 — 저장은 서버 몫이라 종료 화면을 붙잡아 두지 않는다', async () => {
    vi.useFakeTimers();
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    let done = false;
    stt.stop().then(() => {
      done = true;
    });
    expect(JSON.parse(sockets[0]!.sent.at(-1) as string)).toEqual({ type: 'stop' });

    await vi.advanceTimersByTimeAsync(2_999);
    expect(done).toBe(false);
    await vi.advanceTimersByTimeAsync(1);

    expect(done).toBe(true);
    expect(sockets[0]!.closedWith).toBe(1000);
  });

  it('ready 를 기다리다 stop 을 보내도 상한은 stop() 부터 3초다', async () => {
    vi.useFakeTimers();
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();

    let done = false;
    stt.stop().then(() => {
      done = true;
    });

    // 인증이 늦게 끝나 stop 이 2초 뒤에 나갑니다. 여기서 상한을 다시 걸면 5초를 기다립니다
    await vi.advanceTimersByTimeAsync(2_000);
    sockets[0]!.emit(READY);
    expect(JSON.parse(sockets[0]!.sent.at(-1) as string)).toEqual({ type: 'stop' });

    await vi.advanceTimersByTimeAsync(1_000);
    expect(done).toBe(true);
  });

  /**
   * 붙기도 전에 끝냈습니다 (종료 중 새로고침에서 stop 만 보내러 붙는 경우).
   * 버퍼가 비었다고 바로 정리하면 stop 이 안 가고, 서버는 끊긴 연결을 30초 기다립니다.
   */
  it('붙는 중에 끝내면 소켓이 생기기를 기다렸다가 stop 을 보낸다', async () => {
    const stt = makeSocket();
    stt.start();
    const stopped = stt.stop();

    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);
    expect(JSON.parse(sockets[0]!.sent.at(-1) as string)).toEqual({ type: 'stop' });

    sockets[0]!.emit(status('closed'));
    await expect(stopped).resolves.toBeUndefined();
  });
});

describe('서버의 자동 종료', () => {
  const AUTO_END = { type: 'auto_end', reason: 'TIME_LIMIT', message: '연습은 최대 1시간까지예요' };

  it('auto_end 를 알리고, 닫혀도 다시 붙지 않는다', async () => {
    const onAutoEnd = vi.fn();
    const onError = vi.fn();
    const stt = makeSocket({ onAutoEnd, onError });
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    sockets[0]!.emit(AUTO_END);
    expect(onAutoEnd).toHaveBeenCalledExactlyOnceWith('TIME_LIMIT', '연습은 최대 1시간까지예요');

    // 무대가 종료 흐름에서 stop 을 보내도 됩니다. 서버는 이미 정리하고 있습니다
    const stopped = stt.stop();
    sockets[0]!.emit(status('closed'));
    await stopped;
    sockets[0]!.serverClose(1000);
    await settle();

    expect(sockets).toHaveLength(1);
    expect(onError).not.toHaveBeenCalled();
  });

  it('끊겼다 다시 붙을 때 받은 auto_end 도 알리고, 뒤따른 TAKE_ENDED 로 덮지 않는다', async () => {
    const onAutoEnd = vi.fn();
    const onError = vi.fn();
    const onGiveUp = vi.fn();
    const stt = makeSocket({ onAutoEnd, onError, onGiveUp });
    stt.start();
    await settle();
    sockets[0]!.open();

    // 인증 직후 서버가 거절합니다: auto_end → error TAKE_ENDED → 1008
    sockets[0]!.emit(AUTO_END);
    sockets[0]!.emit({ type: 'error', code: 'TAKE_ENDED', message: '연습은 최대 1시간까지예요' });
    sockets[0]!.serverClose(1008);
    await settle();

    expect(onAutoEnd).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
    expect(onGiveUp).toHaveBeenCalledWith('FATAL');
    expect(sockets).toHaveLength(1);
  });

  it('auto_end 뒤에도 연결이 열려 있으면 마지막 metrics 를 바로 보내고, 그 다음에 stop 을 보낸다', async () => {
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    // 서버는 auto_end 를 보낸 뒤 Deepgram 정리 + 2초(FINAL_METRICS_WAIT_SEC) 동안 연결을 열어 둡니다
    sockets[0]!.emit(AUTO_END);
    stt.sendMetrics({ type: 'metrics', t_ms: 3_400, gaze: null, voice: null, slide_number: 1 });
    const stopped = stt.stop();

    const texts = sockets[0]!.sent
      .filter((d): d is string => typeof d === 'string')
      .map((d) => JSON.parse(d) as object);
    // 바로 보냈으니 buffered 가 아닙니다. stop 보다 앞섭니다
    expect(texts.slice(-2)).toEqual([
      { type: 'metrics', t_ms: 3_400, gaze: null, voice: null, slide_number: 1 },
      { type: 'stop' },
    ]);

    sockets[0]!.emit(status('closed'));
    await stopped;
  });

  it('auto_end 뒤 서버가 먼저 닫았으면 마지막 metrics 는 보낼 곳이 없어 버리고, 그 사실을 남긴다', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    sockets[0]!.emit(AUTO_END);
    sockets[0]!.serverClose(1000);
    stt.sendMetrics({ type: 'metrics', t_ms: 3_400, gaze: null, voice: null, slide_number: 1 });

    expect(stt.bufferedMetrics).toBe(0);
    expect(warn).toHaveBeenCalledOnce();
    warn.mockRestore();
  });
});

describe('stop 만 보내기 (종료 중 새로고침)', () => {
  it('인증하고 stop 만 보낸 뒤 closed 를 받으면 끝난다 — 오디오는 없다', async () => {
    const stopped = stopTakeStream('t1', {
      getToken: () => Promise.resolve('access-token'),
      createSocket: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket;
      },
    });

    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);
    expect(sockets[0]!.sent.map((m) => JSON.parse(m as string).type)).toEqual(['auth', 'stop']);

    sockets[0]!.emit(status('closed'));
    await expect(stopped).resolves.toBeUndefined();
  });

  it('토큰을 못 받아도 던지지 않고 끝난다 — 종료는 이것 없이도 이어간다', async () => {
    const stopped = stopTakeStream('t1', {
      getToken: () => Promise.resolve(null),
      createSocket: () => {
        throw new Error('붙으면 안 됩니다');
      },
    });
    await expect(stopped).resolves.toBeUndefined();
  });
});
