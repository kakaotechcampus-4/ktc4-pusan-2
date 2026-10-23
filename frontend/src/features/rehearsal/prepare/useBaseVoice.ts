import { useCallback, useEffect, useRef, useState } from 'react';
import {
  EMPTY_RUN,
  MAX_READ_MS,
  MIN_VOICED_MS,
  addRecord,
  baseLevelDb,
  type BaseVoiceRun,
} from '../media/baseVoice';
import type { VoiceRecord } from '../media/voiceRecord';
import type { Ms } from '@/types/api';

/** 읽는 동안 이 간격으로 음량 기록을 받아 더합니다 */
const POLL_MS = 250;

/**
 *   IDLE     아직 안 잼 (선택 단계라 막지 않습니다)
 *   READING  문장을 읽는 중
 *   DONE     잼 — `levelDb` 가 있습니다
 *   SHORT    시간 안에 충분히 말하지 못함 — `levelDb` 는 null, AI 가 Take 첫 발화로 잡습니다
 */
export type BaseVoiceStatus = 'IDLE' | 'READING' | 'DONE' | 'SHORT';

interface State {
  status: BaseVoiceStatus;
  /** 지금까지 말한 시간을 초로 내림한 값. 진행 표시용이라 초가 바뀔 때만 갱신합니다 */
  voicedSec: number;
  levelDb: number | null;
}

const IDLE: State = { status: 'IDLE', voicedSec: 0, levelDb: null };

/**
 * 발표 전 점검의 "평소 목소리로 읽기" (`media/baseVoice.ts`).
 *
 * 마이크 계량기(`useMicLevel`)가 쌓는 음량 기록에서 읽는 동안의 구간을 받아 더합니다 —
 * 발표 중 음량 1초 기록과 같은 계량기 · 같은 기준입니다. `stream` 이 바뀌면(마이크를 바꿈)
 * 잰 값을 버립니다. 다른 마이크로 잰 평소 목소리는 이 마이크의 기준이 아닙니다.
 */
export function useBaseVoice({
  stream,
  clock,
  voiceRecord,
}: {
  stream: MediaStream | null;
  clock: () => Ms;
  voiceRecord: (startMs: Ms, endMs: Ms) => VoiceRecord;
}) {
  const [state, setState] = useState<State & { stream: MediaStream | null }>({
    ...IDLE,
    stream,
  });
  // 마이크가 바뀌면 렌더 중에 되돌립니다 — 이펙트에서 되돌리면 한 번은 옛 값이 보입니다
  if (state.stream !== stream) setState({ ...IDLE, stream });

  /** 읽기 한 번의 진행. 0.25초마다 바뀌어서 상태로 올리지 않습니다 */
  const runRef = useRef<{ fromMs: Ms; startedAt: Ms; run: BaseVoiceRun } | null>(null);

  const start = useCallback(() => {
    const now = clock();
    runRef.current = { fromMs: now, startedAt: now, run: EMPTY_RUN };
    setState({ status: 'READING', voicedSec: 0, levelDb: null, stream });
  }, [clock, stream]);

  const reading = state.status === 'READING';
  useEffect(() => {
    if (!reading) return;
    const timer = setInterval(() => {
      const r = runRef.current;
      if (!r) return;
      const now = clock();
      r.run = addRecord(r.run, voiceRecord(r.fromMs, now));
      r.fromMs = now;

      if (r.run.voicedMs >= MIN_VOICED_MS) {
        setState((s) => ({ ...s, status: 'DONE', voicedSec: 0, levelDb: baseLevelDb(r.run) }));
        return;
      }
      if (now - r.startedAt > MAX_READ_MS) {
        setState((s) => ({ ...s, status: 'SHORT', voicedSec: 0, levelDb: null }));
        return;
      }
      const voicedSec = Math.floor(r.run.voicedMs / 1000);
      setState((s) => (s.voicedSec === voicedSec ? s : { ...s, voicedSec }));
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [reading, clock, voiceRecord]);

  const cancel = useCallback(() => {
    runRef.current = null;
    setState({ ...IDLE, stream });
  }, [stream]);

  return {
    status: state.status,
    voicedSec: state.voicedSec,
    /** 잰 평소 목소리 크기(dBFS). 안 쟀거나 모자라면 null — 그대로 서버에 보냅니다 */
    levelDb: state.levelDb,
    start,
    cancel,
  };
}
