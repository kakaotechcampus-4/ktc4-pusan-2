import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FRAME_SAMPLES } from './sttProtocol';
import { SttSocket, type SocketLike } from './sttSocket';

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

  it('세 번 실패하면 포기한다', async () => {
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
    expect(onGiveUp).toHaveBeenCalledWith('RETRIES_EXHAUSTED');
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
    // 서버가 정리 도중 끊긴 경우입니다. 여기서 안 풀리면 종료 화면이 15초 멈춰 있습니다
    sockets[0]!.serverClose(1006);

    await expect(stopped).resolves.toBeUndefined();
    expect(sockets).toHaveLength(1);
  });

  it('closed 가 안 오면 15초 뒤에 끊는다 — 종료 화면을 붙잡아 두지 않는다', async () => {
    vi.useFakeTimers();
    const stt = makeSocket();
    stt.start();
    await settle();
    sockets[0]!.open();
    sockets[0]!.emit(READY);

    const stopped = stt.stop();
    await vi.advanceTimersByTimeAsync(15_000);

    await expect(stopped).resolves.toBeUndefined();
    expect(sockets[0]!.closedWith).toBe(1000);
  });
});
