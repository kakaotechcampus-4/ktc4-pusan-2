import { useCallback, useEffect, useRef, useState } from 'react';
import {
  askMore,
  judgeRead,
  readStage,
  startRead,
  stepRead,
  type ReadStage,
  type ReadState,
  type ShortReason,
} from '../media/baseVoice';
import type { VoiceRecord } from '../media/voiceRecord';
import type { Ms } from '@/types/api';

/** 읽는 동안 이 간격으로 음량 기록을 받아 더합니다 */
const POLL_MS = 250;

/**
 *   IDLE     아직 안 잼 (선택 단계라 막지 않습니다)
 *   READING  문장을 읽는 중
 *   DONE     잼 — `levelDb` 가 있습니다
 *   SHORT    다시 읽어야 함 — `reason` 에 이유, `levelDb` 는 null (AI 가 Take 첫 발화로 잡습니다)
 */
export type BaseVoiceStatus = 'IDLE' | 'READING' | 'DONE' | 'SHORT';

interface State {
  status: BaseVoiceStatus;
  /** 읽는 동안의 단계 — 화면이 "지금 무엇을 기다리나"를 단계 체크로 보여 줍니다 (`readStage`) */
  stage: ReadStage;
  /** 보여 줄 문장 수. 다 읽었는데 모자라면 하나씩 늘려 이어 읽게 합니다 */
  sentences: number;
  /** 이어 읽기를 청했나 — 화면이 "조금만 더 들려주세요"를 띄웁니다 */
  askedMore: boolean;
  /** SHORT 일 때 그 이유. 그 밖에는 null */
  reason: ShortReason | null;
  /** DONE 일 때 들은 시간(초, 소수 한 자리). 결과 문구에 씁니다 */
  heardSec: number | null;
  levelDb: number | null;
}

const IDLE: State = {
  status: 'IDLE',
  stage: 'WAITING',
  sentences: 1,
  askedMore: false,
  reason: null,
  heardSec: null,
  levelDb: null,
};

/**
 * 발표 전 점검의 "평소 목소리로 읽기" (`media/baseVoice.ts`).
 *
 * 마이크 계량기(`useMicLevel`)가 쌓는 음량 기록에서 읽는 동안의 구간을 받아 더합니다 —
 * 발표 중 음량 1초 기록과 같은 계량기 · 같은 기준입니다. 끝낼지는 `judgeRead` 가 정합니다
 * (최소 시간을 채운 뒤 말이 멈추면 · 채우기 전에 멈추면 다음 문장 · 띄엄띄엄이면 다시). `stream` 이 바뀌면(마이크를 바꿈)
 * 잰 값을 버립니다. 다른 마이크로 잰 평소 목소리는 이 마이크의 기준이 아닙니다.
 */
export function useBaseVoice({
  stream,
  clock,
  voiceRecord,
  totalSentences,
}: {
  stream: MediaStream | null;
  clock: () => Ms;
  voiceRecord: (startMs: Ms, endMs: Ms) => VoiceRecord;
  /** 준비한 문장 수. 이만큼 이어 읽고도 모자라면 끝냅니다 */
  totalSentences: number;
}) {
  const [state, setState] = useState<State & { stream: MediaStream | null }>({
    ...IDLE,
    stream,
  });
  // 마이크가 바뀌면 렌더 중에 되돌립니다 — 이펙트에서 되돌리면 한 번은 옛 값이 보입니다
  if (state.stream !== stream) setState({ ...IDLE, stream });

  /** 읽기 한 번의 진행. 0.25초마다 바뀌어서 상태로 올리지 않습니다 */
  const runRef = useRef<{ fromMs: Ms; read: ReadState } | null>(null);

  const start = useCallback(() => {
    const now = clock();
    runRef.current = { fromMs: now, read: startRead(now) };
    setState({ ...IDLE, status: 'READING', stream });
  }, [clock, stream]);

  const reading = state.status === 'READING';
  useEffect(() => {
    if (!reading) return;
    const timer = setInterval(() => {
      const r = runRef.current;
      if (!r) return;
      const now = clock();
      const rec = voiceRecord(r.fromMs, now);
      r.read = stepRead(r.read, rec, r.fromMs, now);
      r.fromMs = now;

      const verdict = judgeRead(r.read, now, totalSentences);
      if (verdict.kind === 'DONE') {
        const heardSec = Math.round(verdict.heardMs / 100) / 10;
        setState((s) => ({
          ...s,
          status: 'DONE',
          stage: 'ENOUGH',
          heardSec,
          levelDb: verdict.levelDb,
        }));
        return;
      }
      if (verdict.kind === 'MORE') {
        r.read = askMore(r.read, now);
        const sentences = r.read.sentences;
        setState((s) => ({ ...s, sentences, askedMore: true }));
        return;
      }
      if (verdict.kind === 'SHORT') {
        setState((s) => ({ ...s, status: 'SHORT', reason: verdict.reason }));
        return;
      }
      // 단계가 바뀔 때만 다시 그립니다 — 읽는 동안 두 번 바뀝니다
      const stage = readStage(r.read.run);
      setState((s) => (s.stage === stage ? s : { ...s, stage }));
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [reading, clock, voiceRecord, totalSentences]);

  const cancel = useCallback(() => {
    runRef.current = null;
    setState({ ...IDLE, stream });
  }, [stream]);

  return {
    status: state.status,
    stage: state.stage,
    sentences: state.sentences,
    askedMore: state.askedMore,
    reason: state.reason,
    heardSec: state.heardSec,
    /** 잰 평소 목소리 크기(dBFS). 안 쟀거나 다시 읽어야 하면 null — 그대로 서버에 보냅니다 */
    levelDb: state.levelDb,
    start,
    cancel,
  };
}
