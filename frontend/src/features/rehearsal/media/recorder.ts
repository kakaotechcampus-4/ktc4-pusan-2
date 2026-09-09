import { appendAudioChunk } from '@/shared/lib/db';
import type { Ms } from '@/types/api';

/**
 * 마이크 녹음 — **이게 원본입니다** (CLAUDE.md 4번).
 *
 * 서버로 실시간으로 흘려보내는 PCM 은 덤입니다. 웹소켓이 끊겨도 여기 남은 것으로
 * 발표 후 배치 처리를 할 수 있어야 합니다. 그래서 이 경로가 먼저입니다.
 *
 * ── 5초 조각으로 받는 이유 ──────────────────────────────────────────
 *
 * `MediaRecorder.start()` 를 인자 없이 부르면 stop 할 때 **한 덩이로** 줍니다.
 * 10분 발표 중간에 탭이 죽으면 그 10분이 전부 사라집니다.
 * `start(5000)` 으로 5초마다 받아 즉시 IndexedDB 에 넘기면, 죽어도 마지막 5초만 잃습니다.
 *
 * ── 메모리에 들고 있지 않는 이유 ────────────────────────────────────
 *
 * 조각을 배열에 모아 두면 10분치 수십 MB 가 힙에 남습니다. 그 상태에서
 * 시선 워커가 프레임을 돌리면 GC 압력이 fps 로 나타납니다. 받는 즉시 넘깁니다.
 *
 * ── webm/opus 로 남는다는 것 ────────────────────────────────────────
 *
 * `MediaRecorder` 가 뱉는 webm/opus 는 **스트리밍 STT 에 못 씁니다.**
 * 서버로 보내는 경로는 AudioWorklet → Int16 16kHz 다운샘플이고 그건 별개입니다.
 * 그래서 오디오 경로가 둘입니다 — 이 파일은 원본 보관 쪽만 담당합니다.
 */

/** 조각 길이. 죽었을 때 잃는 최대 분량이기도 합니다. */
export const TIMESLICE_MS = 5000;

/**
 * 후보를 순서대로 시험합니다. Chrome 은 webm/opus, Safari 는 mp4 만 됩니다.
 * 하나도 안 되면 녹음을 시작하지 않고 그 사실을 알립니다 — 조용히 빈 파일이
 * 남는 것보다 낫습니다.
 */
const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];

function pickMimeType(): string | null {
  if (typeof MediaRecorder === 'undefined') return null;
  return MIME_CANDIDATES.find((t) => MediaRecorder.isTypeSupported(t)) ?? null;
}

export type RecorderError = 'UNSUPPORTED' | 'NO_AUDIO_TRACK' | 'RECORDER_FAILED';

export interface RecordingHandle {
  readonly mimeType: string;
  /** 지금까지 넘긴 조각 수 */
  readonly chunkCount: number;
  /** 마지막 조각까지 받고 멈춥니다 */
  stop(): Promise<void>;
}

export interface StartRecordingResult {
  handle: RecordingHandle | null;
  error: RecorderError | null;
}

/**
 * 녹음을 시작합니다.
 *
 * @param stream   getUserMedia 가 준 스트림. 오디오 트랙이 있어야 합니다
 * @param clientSessionId  조각의 키. IndexedDB 의 세션과 같은 값입니다
 * @param startedAtPerf    발표 시작 시각(`performance.now()` 기준).
 *                         조각 오프셋을 이 시각에서 뺍니다 — 시선 판정의 tMs 와
 *                         같은 기준이어야 나중에 두 기록을 겹쳐 볼 수 있습니다
 * @param onError          녹음이 도중에 죽었을 때. 발표는 계속 갑니다
 */
export function startRecording(
  stream: MediaStream,
  clientSessionId: string,
  startedAtPerf: number,
  onError?: (e: RecorderError) => void,
): StartRecordingResult {
  if (stream.getAudioTracks().length === 0) {
    return { handle: null, error: 'NO_AUDIO_TRACK' };
  }

  const mimeType = pickMimeType();
  if (!mimeType) return { handle: null, error: 'UNSUPPORTED' };

  let recorder: MediaRecorder;
  try {
    recorder = new MediaRecorder(stream, { mimeType });
  } catch {
    return { handle: null, error: 'RECORDER_FAILED' };
  }

  let seq = 0;
  /** 다음 조각이 시작되는 오프셋. 첫 조각은 0 */
  let nextOffsetMs: Ms = 0;

  recorder.ondataavailable = (e) => {
    // 크기 0 조각은 버립니다 — stop 직후에 빈 것이 한 번 옵니다
    if (e.data.size === 0) return;

    const offsetMs = nextOffsetMs;
    // 다음 조각의 시작은 **실제 경과 시각**입니다. TIMESLICE_MS 를 더하면
    // 타이머가 밀린 만큼 오프셋이 실제와 어긋나 누적됩니다.
    nextOffsetMs = Math.round(performance.now() - startedAtPerf);

    seq += 1;
    // 받는 즉시 넘깁니다. 여기서 await 하지 않는 이유는 ondataavailable 을
    // 막으면 다음 조각이 밀리기 때문입니다.
    void appendAudioChunk(clientSessionId, seq, offsetMs, e.data);
  };

  recorder.onerror = () => onError?.('RECORDER_FAILED');

  // ★ start() 가 throw 할 수 있다. isTypeSupported 가 true 라도 실제 인코더가
  //   없으면 NotSupportedError 가 난다 (헤드리스 Chrome 에서 실측).
  //   여기서 잡지 않으면 호출부의 async 체인으로 새어나가 **녹음뿐 아니라
  //   그 뒤에 시작하려던 것들까지 조용히 죽는다.**
  try {
    recorder.start(TIMESLICE_MS);
  } catch {
    return { handle: null, error: 'RECORDER_FAILED' };
  }

  return {
    handle: {
      mimeType,
      get chunkCount() {
        return seq;
      },
      stop() {
        return new Promise<void>((resolve) => {
          if (recorder.state === 'inactive') {
            resolve();
            return;
          }
          // stop() 은 마지막 조각을 ondataavailable 로 한 번 더 흘립니다.
          // onstop 이 그 뒤에 오므로 여기서 기다려야 마지막 5초가 안 잘립니다.
          recorder.onstop = () => resolve();
          recorder.stop();
        });
      },
    },
    error: null,
  };
}

export const RECORDER_ERROR_MESSAGE: Record<RecorderError, string> = {
  UNSUPPORTED: '이 브라우저는 녹음을 지원하지 않아요. Chrome이나 Edge로 열어 주세요.',
  NO_AUDIO_TRACK: '마이크가 연결되지 않았어요. 시선 측정은 계속되지만 말하기 분석은 빠집니다.',
  RECORDER_FAILED: '녹음이 중단됐어요. 발표는 계속 진행하셔도 됩니다.',
};
