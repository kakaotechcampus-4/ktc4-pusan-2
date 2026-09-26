import { apiBase, getAccessToken, refresh } from '@/shared/api/tokenStore';
import type { Ms } from '@/types/api';
import {
  buildAudioFrame,
  isFatalError,
  parseServerMessage,
  WS_CLOSE_NORMAL,
  WS_CLOSE_POLICY,
  type SttErrorCode,
  type SttState,
  type SttStatusMessage,
  type TranscriptMessage,
} from './sttProtocol';

/**
 * 실시간 STT 소켓. **React 를 모릅니다** — 상태기계만 있습니다.
 *
 * 무대에 직접 붙이지 않고 이 층을 따로 둔 이유는, 여기 있는 규칙들이 전부
 * "발표 중에 한 번 틀리면 되돌릴 수 없는" 종류라 테스트가 필요하기 때문입니다.
 * 버퍼 순서·seq 연속성·close code 분기·종료 대기 — 넷 다 눈으로는 못 봅니다.
 *
 * ── 이 파일이 지키는 계약 ──────────────────────────────────────────
 *
 *   1. `ready` 전에는 오디오를 보내지 않는다. 대신 2초까지 들고 있다가
 *      **버퍼를 먼저 순서대로** 흘리고 실시간으로 전환한다.
 *      (OPEN 만 보고 보내면 새 프레임이 버퍼를 앞질러 나가고, 서버는 역행한
 *       seq 를 버린다 — 그만큼 전사가 사라진다)
 *   2. `seq` 는 Take 안에서 단조 증가한다. **재연결해도 리셋하지 않는다.**
 *   3. close 1000·1008 이면 재연결하지 않는다. 그 외에는 백오프 3회.
 *   4. `stop` 을 보낸 뒤 `stt_status.state === 'closed'` 를 받기 전에 닫지 않는다.
 *      서버 정리 상한이 12초라 15초까지 기다리고, 그 뒤에는 끊는다.
 *
 * 끊겨도 발표는 계속됩니다. 이 소켓이 죽었을 때 멈추는 것은 2단 코치뿐입니다.
 */

/** 재연결될 때까지 들고 있을 프레임 수. 100ms × 20 = 2초 (BE 와 합의한 계약) */
const MAX_PENDING_FRAMES = 20;

/** 백오프. 길이가 곧 재연결 횟수 상한입니다 */
const RETRY_DELAYS_MS = [500, 1_000, 2_000];

/** `stop` 뒤 `closed` 를 기다리는 상한. 서버 쪽 상한(12초)보다 조금 깁니다 */
const STOP_TIMEOUT_MS = 15_000;

/**
 * 끊긴 채로 발표를 끝냈을 때, 남은 버퍼를 보내려고 재연결을 기다리는 상한.
 *
 * 전체 상한(15초)을 쓰지 않습니다 — 네트워크가 정말 죽었으면 끝내기 버튼을 누른 사람이
 * 15초를 서서 기다리게 됩니다. 백오프 한 번(최대 2초)이 지나갈 만큼만 줍니다.
 */
const STOP_RECONNECT_BUDGET_MS = 3_000;

/**
 * 테스트에서 가짜를 끼우려고 최소한만 추려낸 WebSocket 모양입니다.
 * 실제로 쓰는 것은 여기 있는 것이 전부입니다.
 */
export interface SocketLike {
  send(data: string | ArrayBufferView): void;
  close(code?: number, reason?: string): void;
  onopen: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number; reason?: string }) => void) | null;
  onerror: (() => void) | null;
}

export type GiveUpReason = 'FATAL' | 'RETRIES_EXHAUSTED' | 'NO_TOKEN' | 'CONNECT_FAILED';

export interface SttSocketOptions {
  takeId: string;
  onTranscript: (message: TranscriptMessage) => void;
  /** 화면에 뜨는 상태. 서버가 알려준 값과 우리가 아는 값(재연결 중)이 함께 옵니다 */
  onState: (state: SttState) => void;
  onStatus?: (message: SttStatusMessage) => void;
  onError?: (code: SttErrorCode, message: string) => void;
  /** 더 붙지 않습니다. 화면은 이 시점에 "말하기 분석 없이 계속"으로 넘어갑니다 */
  onGiveUp?: (reason: GiveUpReason) => void;
  createSocket?: (url: string) => SocketLike;
  getToken?: (options: { renew: boolean }) => Promise<string | null>;
}

/**
 * `/api` 가 붙습니다 — Caddy 가 `/api/*` 만 백엔드로 넘기기 때문에
 * 문서의 `/ws/takes/{id}` 를 그대로 쓰면 프론트 정적 서버로 갑니다.
 */
export function sttUrl(takeId: string): string {
  const base = apiBase || globalThis.location?.origin || '';
  return `${base.replace(/^http/, 'ws')}/api/ws/takes/${encodeURIComponent(takeId)}`;
}

/**
 * 캐시된 토큰이 있으면 그대로, 없으면 갱신합니다.
 *
 * `renew` 는 **캐시를 건너뜁니다.** 메모리에 든 access 토큰은 15분짜리라
 * 긴 발표 중에 만료될 수 있는데, 그걸 그대로 다시 들고 붙으면 서버가
 * `UNAUTHORIZED` 로 닫고 FE 는 치명 오류로 보고 영영 포기합니다 —
 * 실제로는 새로 받기만 하면 되는 상황입니다.
 */
async function defaultGetToken({ renew }: { renew: boolean }): Promise<string | null> {
  if (renew) return refresh().catch(() => null);

  const token = getAccessToken();
  if (token) return token;

  return refresh().catch(() => null);
}

export class SttSocket {
  private readonly options: SttSocketOptions;
  private socket: SocketLike | null = null;

  /** Take 단위 누적입니다. 재연결에서 리셋하지 않습니다 (계약 2) */
  private seq = 0;
  private pending: Uint8Array[] = [];
  private ready = false;

  private retries = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;

  /** 닫혀도 다시 붙지 않는 상태. 에러 코드가 정합니다 */
  private fatal = false;

  /** 토큰을 새로 받아 한 번 더 붙어 볼 것인가 (만료된 토큰과 진짜 인증 실패를 가릅니다) */
  private renewToken = false;
  private authRetried = false;
  private disposed = false;

  private stopping = false;
  private stopTimer: ReturnType<typeof setTimeout> | null = null;
  private stopResolvers: (() => void)[] = [];

  constructor(options: SttSocketOptions) {
    this.options = options;
  }

  /** 지금까지 보낸(또는 버린) 프레임 수. 화면 계기판용입니다 */
  get sentFrames(): number {
    return this.seq;
  }

  get bufferedFrames(): number {
    return this.pending.length;
  }

  start(): void {
    if (this.disposed || this.socket) return;
    this.connect().catch(() => this.giveUp('CONNECT_FAILED'));
  }

  /**
   * 프레임 한 장. `ready` 전이면 버퍼에 쌓입니다.
   *
   * 버퍼가 넘치면 **오래된 것부터** 버립니다. 발표는 계속 진행되고 있으니
   * 최근 2초를 살리는 쪽이 낫습니다.
   */
  sendFrame(pcm: ArrayBuffer, offsetMs: Ms): void {
    if (this.disposed || this.stopping) return;

    this.seq += 1;
    const frame = buildAudioFrame(this.seq, offsetMs, pcm);

    if (this.ready && this.socket) {
      this.socket.send(frame);
      return;
    }

    this.pending.push(frame);
    while (this.pending.length > MAX_PENDING_FRAMES) this.pending.shift();
  }

  /**
   * 발표 종료. `closed` 를 받거나 상한이 지나면 풀립니다.
   *
   * 여기서 기다리지 않고 닫으면 **마지막 1~2초의 전사가 사라집니다** —
   * 발표의 마무리 문장이라 리포트에서 가장 아쉬운 자리입니다.
   *
   * ── 끊긴 채로 끝낸 경우 ────────────────────────────────────────────
   *
   * 재연결을 기다리는 중이면 아직 못 보낸 버퍼가 남아 있고, 그 2초가 바로 마무리
   * 문장입니다. 그래서 **예약된 재연결을 취소하지 않고** 짧게 기다립니다 —
   * 다시 붙으면 `ready` 뒤에 버퍼를 흘리고 그때 `stop` 을 보냅니다.
   * 서버는 끊긴 뒤 30초 동안 스트림을 살려 두므로 번호도 이어집니다.
   *
   * 그 시도마저 실패하면(다시 `close`) 곧바로 정리합니다. 남은 버퍼는 잃지만,
   * 종료를 더 붙잡아 두면 사용자가 발표 끝내기에서 멈춰 기다리게 됩니다.
   */
  stop(): Promise<void> {
    if (this.disposed) {
      this.dispose();
      return Promise.resolve();
    }

    if (!this.stopping) {
      this.stopping = true;

      if (this.socket) {
        this.clearRetry();
        this.armStopTimer(STOP_TIMEOUT_MS);
        this.sendStop();
      } else if (this.pending.length > 0 && this.retryTimer !== null) {
        // 예약된 재연결을 그대로 둡니다. 붙으면 sendStop 이 버퍼부터 흘립니다
        this.armStopTimer(STOP_RECONNECT_BUDGET_MS);
      } else {
        this.dispose();
        return Promise.resolve();
      }
    }

    return new Promise((resolve) => this.stopResolvers.push(resolve));
  }

  private armStopTimer(afterMs: number): void {
    if (this.stopTimer !== null) clearTimeout(this.stopTimer);
    this.stopTimer = setTimeout(() => this.dispose(), afterMs);
  }

  /**
   * 버퍼를 먼저 비우고 `stop` 을 보냅니다.
   *
   * 아직 `ready` 가 아니면 **보내지 않고 기다립니다.** 인증이 끝나기 전의 오디오는
   * 서버가 받지 않으므로, 여기서 stop 부터 보내면 들고 있던 2초가 그대로 버려집니다
   * (발표를 짧게 끝냈거나 마지막 순간에 재연결 중이던 경우). `ready` 가 오면
   * `handleMessage` 가 이 함수를 다시 부르고, 안 오면 15초 타임아웃이 끊습니다.
   */
  private sendStop(): void {
    if (!this.socket || !this.ready) return;

    try {
      this.flushPending();
      this.socket.send(JSON.stringify({ type: 'stop' }));
      // 여기서부터는 서버가 Deepgram 을 정리하는 시간입니다. 상한을 그쪽에 맞춥니다
      this.armStopTimer(STOP_TIMEOUT_MS);
    } catch {
      this.dispose();
    }
  }

  /** 화면을 떠날 때. 기다리지 않고 바로 놓습니다 */
  dispose(): void {
    if (this.disposed) {
      this.flushStopResolvers();
      return;
    }
    this.disposed = true;

    this.clearRetry();
    if (this.stopTimer !== null) clearTimeout(this.stopTimer);
    this.stopTimer = null;

    this.detach(this.socket);
    try {
      this.socket?.close(WS_CLOSE_NORMAL);
    } catch {
      // 이미 닫혔습니다. 정리만 하면 됩니다
    }
    this.socket = null;
    this.ready = false;
    this.pending = [];

    this.flushStopResolvers();
  }

  private async connect(): Promise<void> {
    const getToken = this.options.getToken ?? defaultGetToken;
    const renew = this.renewToken;
    this.renewToken = false;

    const token = await getToken({ renew });
    // await 사이에 화면을 떠났을 수 있습니다
    if (this.disposed) return;

    if (!token) {
      this.giveUp('NO_TOKEN');
      return;
    }

    this.options.onState(this.retries === 0 ? 'connecting' : 'reconnecting');

    const create = this.options.createSocket ?? ((url: string) => new WebSocket(url) as SocketLike);
    const socket = create(sttUrl(this.options.takeId));
    this.socket = socket;
    this.ready = false;

    // 브라우저 WebSocket 은 Authorization 헤더를 못 붙입니다. 그래서 첫 메시지로 보냅니다
    socket.onopen = () => socket.send(JSON.stringify({ type: 'auth', token }));
    socket.onmessage = (event) => this.handleMessage(event.data);
    socket.onclose = (event) => this.handleClose(event.code);
    socket.onerror = () => undefined;
  }

  private handleMessage(data: unknown): void {
    const message = parseServerMessage(data);
    // 모르는 메시지는 버립니다. 서버가 나중에 metrics·coach 를 추가해도 안 깨집니다
    if (!message) return;

    switch (message.type) {
      case 'ready':
        this.retries = 0;
        this.flushPending();
        this.ready = true;
        this.options.onState(message.stt_state);
        // 인증을 기다리느라 미뤄 둔 종료가 있으면 이제 보냅니다
        if (this.stopping) this.sendStop();
        break;

      case 'transcript':
        this.options.onTranscript(message);
        break;

      case 'stt_status':
        this.options.onStatus?.(message);
        this.options.onState(message.state);
        // 서버가 정리를 끝냈습니다. 이제 닫아도 마지막 전사를 잃지 않습니다
        if (message.state === 'closed') this.dispose();
        break;

      case 'error':
        this.options.onError?.(message.code, message.message);

        // 토큰이 만료된 것뿐일 수 있습니다. 새로 받아 **한 번만** 다시 붙어 봅니다 —
        // 그래도 거절당하면 진짜 인증 실패이므로 그때 포기합니다
        if (message.code === 'UNAUTHORIZED' && !this.authRetried) {
          this.authRetried = true;
          this.renewToken = true;
          break;
        }

        // 에러가 곧 종료는 아닙니다. 치명적인 것만 재연결을 막습니다
        if (isFatalError(message.code)) this.fatal = true;
        break;
    }
  }

  /**
   * 버퍼를 **먼저** 비우고 그 다음에 실시간을 엽니다 (계약 1).
   * 순서가 뒤집히면 서버가 낮은 seq 를 역행으로 보고 버립니다.
   */
  private flushPending(): void {
    if (!this.socket) return;

    for (const frame of this.pending) this.socket.send(frame);
    this.pending = [];
  }

  private handleClose(code: number): void {
    this.detach(this.socket);
    this.socket = null;
    this.ready = false;

    if (this.disposed) return;

    // stop 을 보낸 뒤라면 서버가 정리를 마치고 닫은 것입니다
    if (this.stopping) {
      this.dispose();
      return;
    }

    // 토큰 갱신이 예약돼 있으면 1008 이어도 한 번 더 붙습니다 (백오프 없이 바로)
    if (!this.fatal && this.renewToken) {
      this.options.onState('reconnecting');
      this.connect().catch(() => this.giveUp('CONNECT_FAILED'));
      return;
    }

    if (this.fatal || code === WS_CLOSE_NORMAL || code === WS_CLOSE_POLICY) {
      this.giveUp('FATAL');
      return;
    }

    const delay = RETRY_DELAYS_MS[this.retries];
    if (delay === undefined) {
      this.giveUp('RETRIES_EXHAUSTED');
      return;
    }

    this.retries += 1;
    this.options.onState('reconnecting');
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      if (!this.disposed) this.connect().catch(() => this.giveUp('CONNECT_FAILED'));
    }, delay);
  }

  /**
   * 더 붙지 않습니다. 상태는 `degraded` 로 둡니다 — `closed` 는 "정상적으로
   * 정리가 끝났다"는 뜻이라, 여기서 쓰면 화면이 아무 문제 없었던 것처럼 보입니다.
   */
  private giveUp(reason: GiveUpReason): void {
    this.pending = [];
    this.options.onState('degraded');
    this.options.onGiveUp?.(reason);
    this.dispose();
  }

  private clearRetry(): void {
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = null;
  }

  private detach(socket: SocketLike | null): void {
    if (!socket) return;
    socket.onopen = null;
    socket.onmessage = null;
    socket.onclose = null;
    socket.onerror = null;
  }

  private flushStopResolvers(): void {
    const resolvers = this.stopResolvers;
    this.stopResolvers = [];
    resolvers.forEach((resolve) => resolve());
  }
}
