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
import type { GazeSampleRecord } from '@/workers/gaze.contract';
import type { VoiceRecord } from '../media/voiceRecord';

// ── FE → BE (JSON) ─────────────────────────────────────────────────

/**
 * 1초마다 보내는 측정값 (`docs/ai/architecture.md` 1-4 · 6-2, BE #255).
 *
 *   t_ms          이 1초가 끝난 시각 (Take 시작 기준 ms). 기록의 `t_ms` 는 그 1초의 시작입니다
 *   gaze          시선 1초 기록 — 엔진 `sampleToDict` 결과 그대로. 잴 수 없었으면 null
 *   voice         음량 1초 기록 (`voiceRecord.ts`). 시선 기록과 같은 구간입니다
 *   slide_number  지금 슬라이드 번호 (1부터). 모르면 null
 *   buffered      끊긴 동안 쌓아 두었다가 다시 붙은 뒤 보내는 것만 true. 바로 보내는 것에는 붙이지
 *                 않습니다(없으면 false). 소켓이 보낼 때 붙입니다 (`SttSocket.sendMetrics`)
 *
 * 받은 BE 는 1초 원자료로 저장하고(Take 결과의 재료) 실시간 코치를 부릅니다. `buffered` 인 것은
 * 저장만 하고 코치를 부르지 않습니다 — 지난 시각으로 말을 걸지 않게 (`architecture.md` 2절).
 * `stop` 직전에 마지막 1초 조각(1초보다 짧을 수 있음)을 한 번 더 보냅니다.
 */
export interface MetricsMessage {
  type: 'metrics';
  t_ms: Ms;
  gaze: GazeSampleRecord | null;
  voice: VoiceRecord | null;
  slide_number: number | null;
  /**
   * CLAUDE.md 6번(`?` 대신 `| null`)의 예외입니다. 계약이 "선택 bool, 없으면 false, 몰아 보낼 때만
   * true 로 붙인다"(`architecture.md` 1-4)라 바로 보내는 것에는 키를 붙이지 않습니다.
   */
  buffered?: true;
}

/**
 * 장이 바뀌었다 (`docs/ai/architecture.md` 1-4, #324 6-4). 장 시각의 기준은 이것 하나입니다 —
 * `metrics.slide_number` 는 참고값입니다.
 *
 *   slide_number  넘어온 장 (1부터)
 *   t_ms          그 장으로 넘어온 Take 시각. 늦게 보내도 실제로 넘긴 시각입니다
 *
 * 보내는 때: Take 시작(첫 장, `t_ms: 0`) · 장을 넘길 때마다 · 새로고침해 이어 갈 때(이어받은 장과
 * 그 장으로 넘어온 원래 시각). BE 는 `(take_id, t_ms)` 로 한 번만 저장합니다.
 */
export interface SlideMessage {
  type: 'slide';
  slide_number: number;
  t_ms: Ms;
}

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

/**
 * BE 가 Take 를 끝낸 이유. `TAKE_ENDED` 에 실려 옵니다 (서버는 사용자가 끝낸 Take 면 필드를 빼고,
 * `parseServerMessage` 가 그때 `null` 로 채웁니다).
 *
 * - `CLOSED` 창을 닫았습니다. **이어 가지 않습니다** — 실패 화면으로 갑니다
 * - `ABANDONED` 창을 닫지 않고 5분 넘게 끊겨 서버가 끝냈습니다. 발표는 STT 없이 이어 가고,
 *   끝내기(PUT)를 보내면 서버가 되살립니다
 * - `SILENCE` · `TIME_LIMIT` 자동 종료 (`AutoEndMessage` 가 먼저 옵니다)
 */
export type TakeEndReason = 'SILENCE' | 'TIME_LIMIT' | 'ABANDONED' | 'CLOSED';

export interface SttErrorMessage {
  type: 'error';
  code: SttErrorCode;
  message: string;
  /** `TAKE_ENDED` 일 때 BE 가 끝낸 이유. 사용자가 끝냈거나 다른 코드면 null */
  end_reason: TakeEndReason | null;
}

/** `SILENCE` 오래 말이 없었다 · `TIME_LIMIT` 최대 시간(1시간)이 지났다 */
export type AutoEndReason = 'SILENCE' | 'TIME_LIMIT';

/**
 * 서버가 Take 를 자동으로 끝냈습니다. 이 뒤로 서버는 오디오를 받지 않고,
 * `stt_status` closed 를 보낸 뒤 1000 으로 닫습니다.
 *
 * 무대는 **끝내기 버튼과 같은 종료 흐름**을 타고 `message` 를 보여 줍니다.
 * 끊겨 있다가 다시 붙을 때도 옵니다 — 그때는 이 메시지 뒤에 `TAKE_ENDED` 가 따라옵니다.
 */
export interface AutoEndMessage {
  type: 'auto_end';
  reason: AutoEndReason;
  /** 화면에 그대로 보여 줄 문구 */
  message: string;
}

/** 상태 표시를 하는 영역 (`docs/ai/architecture.md` 6-3). `CONTENT` 는 발표 중에 판정하지 않습니다 */
export type IndicatorArea = 'GAZE' | 'SPEED' | 'VOLUME' | 'PAUSE' | 'FILLER' | 'TIME';

/** 코치가 말을 거는 영역 */
export type CoachArea = IndicatorArea | 'CONTENT';

/**
 * 코치가 건네는 한마디 (`coach-agent.md` 5-3). `message` 는 AI 코치가 만든 문장이라 그대로 띄웁니다.
 * `instruction` 은 `LOOK_AT_CAMERA` · `SLOW_DOWN` · … · `CONTINUE`(지금처럼 유지) 입니다.
 */
export interface CoachFeedback {
  area: CoachArea;
  instruction: string;
  message: string;
  /** 0–100 */
  priority: number;
  /** 0–1 */
  confidence: number;
}

/**
 * AI 코치(`/coach/evaluate`)의 판단을 BE 가 넘겨줍니다 (`architecture.md` 1-4).
 * BE 는 `COACHING` 모드에서 **말을 걸 때(`INTERVENE`)와 상태 표시가 바뀔 때만** 보냅니다.
 *
 *   feedback    `INTERVENE` 일 때만 객체. 그 밖에는 null
 *   indicators  영역 → 그 판정 모듈의 상태 (`AUDIENCE` · `SCRIPT` · `NORMAL` · `FAST` · `AHEAD` …).
 *               빈 객체는 '바뀌지 않음' 입니다 — 이전 표시를 그대로 둡니다
 *
 * 언제 말할지 · 얼마나 자주 말할지는 AI 코치가 정합니다 (쿨다운 · 효과 판정). FE 는 띄우기만 합니다.
 */
export interface CoachMessage {
  type: 'coach';
  t_ms: Ms;
  /** `INTERVENE` 지금 말함 · `WAIT` 보기만 · `IGNORE` 이번엔 버림 */
  action: string;
  feedback: CoachFeedback | null;
  indicators: Partial<Record<IndicatorArea, string>>;
}

export type ServerMessage =
  | ReadyMessage
  | TranscriptMessage
  | SttStatusMessage
  | SttErrorMessage
  | AutoEndMessage
  | CoachMessage;

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
 * 말하기 분석만 빠지고 시선·시간은 계속 돕니다 (CLAUDE.md 4번).
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

/** `TAKE_ENDED`(`ABANDONED`) — 오래 끊겨 서버가 Take 를 끝냈지만 발표는 이어 갑니다 */
export const STT_ABANDONED_MESSAGE = '연결이 오래 끊겨 말하기 분석이 멈췄어요. 발표는 계속하세요.';

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

const END_REASONS: ReadonlySet<string> = new Set<TakeEndReason>([
  'SILENCE',
  'TIME_LIMIT',
  'ABANDONED',
  'CLOSED',
]);

/** 모르는 이유(서버가 나중에 늘림)는 null 입니다 — 분기할 수 없는 값을 분기한 척하지 않습니다 */
function toEndReason(value: unknown): TakeEndReason | null {
  return typeof value === 'string' && END_REASONS.has(value) ? (value as TakeEndReason) : null;
}

/**
 * 빠진 칸은 '없음' 으로 채웁니다. 말이 없으면 `feedback` 은 null, 표시가 그대로면 `indicators` 는 {}.
 * 띄울 문장이 없는 `feedback` 은 버립니다 — 빈 줄을 띄우는 것보다 아무 말도 안 하는 편이 낫습니다.
 */
function toCoachMessage(raw: Record<string, unknown>): CoachMessage {
  const feedback = raw.feedback as Partial<CoachFeedback> | null | undefined;
  const indicators = raw.indicators;
  return {
    type: 'coach',
    t_ms: typeof raw.t_ms === 'number' ? raw.t_ms : 0,
    action: typeof raw.action === 'string' ? raw.action : 'WAIT',
    feedback:
      feedback && typeof feedback.message === 'string' && feedback.message !== ''
        ? (feedback as CoachFeedback)
        : null,
    indicators:
      typeof indicators === 'object' && indicators !== null && !Array.isArray(indicators)
        ? (indicators as CoachMessage['indicators'])
        : {},
  };
}

const SERVER_MESSAGE_TYPES: ReadonlySet<string> = new Set([
  'ready',
  'transcript',
  'stt_status',
  'error',
  'auto_end',
  'coach',
]);

/**
 * 모르는 메시지는 `null` 입니다. 서버가 나중에 새 메시지를 추가해도
 * 이 화면이 깨지지 않아야 합니다 — 발표 중에 예외가 나면 그걸로 끝입니다.
 */
export function parseServerMessage(raw: unknown): ServerMessage | null {
  if (typeof raw !== 'string') return null;

  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return null;

    const type = (parsed as { type?: unknown }).type;
    if (typeof type !== 'string' || !SERVER_MESSAGE_TYPES.has(type)) return null;

    // 서버는 이유가 없으면 필드를 뺍니다. 없음은 null 로 둡니다 (CLAUDE.md 6번)
    if (type === 'error') {
      const error = parsed as Omit<SttErrorMessage, 'end_reason'> & { end_reason?: unknown };
      return { ...error, end_reason: toEndReason(error.end_reason) };
    }
    if (type === 'coach') return toCoachMessage(parsed as Record<string, unknown>);
    return parsed as ServerMessage;
  } catch {
    return null;
  }
}
