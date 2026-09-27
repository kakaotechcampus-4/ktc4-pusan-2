/**
 * 실시간 STT WebSocket 의 말(프로토콜)만 모아 둔 파일입니다.
 *
 * ── REST 타입과 왜 분리하나 ─────────────────────────────────────────
 *
 * `types/api.ts` 는 camelCase 인데 이쪽은 **snake_case** 입니다. BE 가 REST DTO 와
 * WS DTO 를 따로 두고 있어서(`realtime/dto.py`), 한 파일에 섞으면 어느 쪽 규칙을
 * 따라야 하는지가 매번 헷갈립니다. 여기 있는 이름은 **서버가 보내는 그대로**입니다.
 *
 * 값의 출처는 `backend/src/pitch_coach_backend/realtime/dto.py` 입니다.
 * 문서(`backend/docs/stt-websocket-guide.md`)와 어긋나면 그쪽 코드가 맞습니다 —
 * 문서 §10 은 소유권 검사가 붙기 전에 쓰여서 이미 낡았습니다.
 */

import type { Ms } from '@/types/api';

/**
 * 서버가 가정하는 오디오 규격입니다. **다른 레이트를 보내면 조용히 틀립니다** —
 * 서버는 받은 바이트 수로 시간을 세기 때문에, 48kHz 를 보내면 전사 시각이 3배로
 * 늘어나 리포트 전체가 어긋납니다. 열리지 않으면 보내지 않는 쪽이 맞습니다.
 */
export const STT_SAMPLE_RATE = 16_000;

/** 프레임 하나의 길이. 서버 상한은 1초이고 갭 50ms 까지는 무음으로 채워 줍니다 */
export const FRAME_MS = 100;

export const FRAME_SAMPLES = (STT_SAMPLE_RATE * FRAME_MS) / 1000;
/** `[seq u32 LE][offset_ms u32 LE]` */
export const FRAME_HEADER_BYTES = 8;
export const FRAME_BYTES = FRAME_HEADER_BYTES + FRAME_SAMPLES * 2;

/** 정상 종료. 이 코드로 닫히면 재연결하지 않습니다 */
export const WS_CLOSE_NORMAL = 1000;
/** 인증·권한·Take 검사·중복 연결. 재연결하면 같은 이유로 또 닫힙니다 */
export const WS_CLOSE_POLICY = 1008;

// ── BE → FE ────────────────────────────────────────────────────────

/**
 * `connecting` 아직 Deepgram 에 붙기 전 · `ok` 정상 · `reconnecting` 재접속 중
 * `degraded` 재접속이 이어서 실패 (발표는 계속된다) · `closed` 정리 완료
 */
export type SttState = 'connecting' | 'ok' | 'reconnecting' | 'degraded' | 'closed';

export interface ReadyMessage {
  type: 'ready';
  take_id: string;
  stt_session_no: number;
  stt_state: SttState;
}

export interface TranscriptWord {
  /** 구두점 없는 원본 어절. **군더더기 검출은 이쪽**입니다 */
  word: string;
  /** 구두점이 붙은 어절. 화면 표시는 이쪽입니다 */
  punctuated_word: string;
  start_ms: Ms;
  end_ms: Ms;
  confidence: number;
}

export interface TranscriptMessage {
  type: 'transcript';
  /** 같은 구간의 식별자. 같은 값이 다시 오면 **최신 것으로 덮어씁니다** */
  segment_id: string;
  is_final: boolean;
  speech_final: boolean;
  /** Take 시작 = 0 기준입니다 (Deepgram 세션 기준이 아닙니다) */
  start_ms: Ms;
  end_ms: Ms;
  text: string;
  confidence: number;
  words: TranscriptWord[];
}

export interface SttStatusMessage {
  type: 'stt_status';
  state: SttState;
  stt_session_no: number;
  /** 아래 넷은 연결 단위가 아니라 **Take 누적**입니다 */
  frames: number;
  dropped_frames: number;
  silence_ms: Ms;
  /** STT 에 닿지 못한 오디오. 0 보다 크면 리포트에 미수집 구간이 있습니다 */
  lost_ms: Ms;
}

export type SttErrorCode =
  | 'UNAUTHORIZED'
  | 'TAKE_NOT_FOUND'
  | 'TAKE_ENDED'
  | 'FORBIDDEN'
  | 'BAD_MESSAGE'
  | 'BAD_AUDIO_FRAME'
  | 'TAKE_TAKEN_OVER';

export interface SttErrorMessage {
  type: 'error';
  code: SttErrorCode;
  message: string;
}

export type ServerMessage = ReadyMessage | TranscriptMessage | SttStatusMessage | SttErrorMessage;

/**
 * **에러가 곧 종료는 아닙니다.** 여기 있는 것만 1008 로 닫히고, 나머지
 * (`BAD_MESSAGE`·`BAD_AUDIO_FRAME`)는 연결이 그대로 살아 있습니다.
 * 닫힌 뒤 다시 붙어 봐야 같은 이유로 또 닫히므로 재연결도 하지 않습니다.
 */
const FATAL_CODES: ReadonlySet<string> = new Set<SttErrorCode>([
  'UNAUTHORIZED',
  'TAKE_NOT_FOUND',
  'TAKE_ENDED',
  'FORBIDDEN',
  'TAKE_TAKEN_OVER',
]);

export function isFatalError(code: SttErrorCode): boolean {
  return FATAL_CODES.has(code);
}

/**
 * 화면에 그대로 나가는 문구입니다. **발표를 멈추라고 말하지 않습니다** —
 * 말하기 분석만 빠지고 시선·시간·녹음은 계속 돕니다 (CLAUDE.md 4번).
 */
export const STT_ERROR_MESSAGE: Record<SttErrorCode, string> = {
  UNAUTHORIZED: '로그인이 만료되어 말하기 분석이 멈췄어요. 발표는 계속하세요.',
  TAKE_NOT_FOUND: '이 연습을 찾지 못해 말하기 분석을 켜지 못했어요. 발표는 계속하세요.',
  TAKE_ENDED: '이미 끝난 연습이라 말하기 분석을 켜지 못했어요.',
  FORBIDDEN: '이 연습에 접근할 수 없어 말하기 분석이 멈췄어요. 발표는 계속하세요.',
  BAD_MESSAGE: '말하기 분석에 문제가 생겼어요. 발표는 계속하세요.',
  BAD_AUDIO_FRAME: '소리를 보내는 중 문제가 생겼어요. 발표는 계속하세요.',
  TAKE_TAKEN_OVER: '다른 탭에서 같은 연습을 이어받았어요. 이 탭의 말하기 분석은 멈춥니다.',
};

/**
 * 발밑 한 줄에 쓰는 상태 문구. `null` 이면 아무 말도 하지 않습니다 —
 * 정상일 때 굳이 말을 거는 것도 발표자에게는 방해입니다.
 */
export const STT_STATE_NOTE: Record<SttState, string | null> = {
  connecting: '말하기 분석 연결 중',
  ok: '말하기 분석 중',
  reconnecting: '말하기 분석 재연결 중 — 발표는 계속하세요',
  degraded: '말하기 분석이 잠시 멈췄어요 — 발표는 계속하세요',
  closed: null,
};

// ── FE → BE ────────────────────────────────────────────────────────

/**
 * 오디오 프레임 한 장.
 *
 * ```text
 * [seq u32 LE][offset_ms u32 LE][PCM Int16 LE]
 *      4            4              3,200
 * ```
 *
 * little-endian 인 이유는 서버가 `struct("<II")` 로 읽기 때문입니다. 기본값(big-endian)
 * 으로 쓰면 seq 가 1 이 아니라 16,777,216 으로 도착해 서버가 역행으로 보고 버립니다.
 */
export function buildAudioFrame(seq: number, offsetMs: Ms, pcm: ArrayBuffer): Uint8Array {
  const frame = new Uint8Array(FRAME_HEADER_BYTES + pcm.byteLength);
  const header = new DataView(frame.buffer, 0, FRAME_HEADER_BYTES);

  header.setUint32(0, seq, true);
  header.setUint32(4, offsetMs, true);
  frame.set(new Uint8Array(pcm), FRAME_HEADER_BYTES);

  return frame;
}

const SERVER_MESSAGE_TYPES: ReadonlySet<string> = new Set([
  'ready',
  'transcript',
  'stt_status',
  'error',
]);

/**
 * 모르는 메시지는 `null` 입니다. 서버가 나중에 `metrics`·`coach` 를 추가해도
 * 이 화면이 깨지지 않아야 합니다 — 발표 중에 예외가 나면 그걸로 끝입니다.
 */
export function parseServerMessage(raw: unknown): ServerMessage | null {
  if (typeof raw !== 'string') return null;

  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return null;

    const type = (parsed as { type?: unknown }).type;
    if (typeof type !== 'string' || !SERVER_MESSAGE_TYPES.has(type)) return null;

    return parsed as ServerMessage;
  } catch {
    return null;
  }
}
