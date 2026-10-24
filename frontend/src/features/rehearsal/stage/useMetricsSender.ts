import { useCallback, useEffect, useRef } from 'react';
import { createMetricsClock, type MetricsClock, type MetricsWindow } from '../lib/metricsClock';
import type { MetricsMessage } from '../lib/sttProtocol';
import type { VoiceRecord } from '../media/voiceRecord';
import type { GazeSampleRecord } from '@/workers/gaze.contract';
import type { Ms } from '@/types/api';

/** 시선 기록이 오지 않을 때 스스로 1초를 이을지 이 간격으로 봅니다 */
const TICK_MS = 250;

/**
 * 발표 중 1초마다 WS `metrics` 를 보냅니다 (`docs/ai/architecture.md` 1-4 · 6-2, BE #255).
 *
 * 어느 1초로 보낼지는 `metricsClock` 이 정합니다 — 시선 1초 기록이 오면 그 구간, 안 오면 스스로.
 * 여기서는 그 구간에 음량 1초 기록과 지금 슬라이드 번호를 붙여 소켓에 넘깁니다.
 * 끊겨 있으면 소켓이 쌓아 두었다가 다시 붙을 때 보냅니다 (`SttSocket.sendMetrics`).
 *
 * 슬라이드 번호는 **보내는 순간**의 번호입니다. 1초 기록은 0.2~3초 늦게 나가서 넘긴 직후의
 * 1초는 다음 슬라이드로 적힐 수 있습니다. 슬라이드별 정확한 구간은 슬라이드 기록이 따로 갖습니다.
 */
export function useMetricsSender({
  enabled,
  elapsedMs,
  voiceRecord,
  slideNumber,
  send,
}: {
  /** 발표 중일 때만. 처음 켜질 때의 무대 시각부터 보냅니다 (새로고침해 이어받았으면 그 시각) */
  enabled: boolean;
  elapsedMs: () => Ms;
  voiceRecord: (startMs: Ms, endMs: Ms) => VoiceRecord;
  slideNumber: number | null;
  send: (message: MetricsMessage) => void;
}) {
  const clockRef = useRef<MetricsClock | null>(null);
  const slideRef = useRef(slideNumber);
  useEffect(() => {
    slideRef.current = slideNumber;
  }, [slideNumber]);

  const toMessages = useCallback(
    (windows: MetricsWindow[]): MetricsMessage[] =>
      windows.map((w) => ({
        type: 'metrics',
        t_ms: w.endMs,
        gaze: w.gaze,
        voice: voiceRecord(w.startMs, w.endMs),
        slide_number: slideRef.current,
      })),
    [voiceRecord],
  );

  const emit = useCallback(
    (windows: MetricsWindow[]) => {
      for (const message of toMessages(windows)) send(message);
    },
    [send, toMessages],
  );

  useEffect(() => {
    if (!enabled) return;
    // 한 Take 에 한 번만 만듭니다. 끝내기(ENDING)로 꺼졌다고 버리면 `finish` 가 받을 시계가 없습니다
    clockRef.current ??= createMetricsClock(elapsedMs());
    const clock = clockRef.current;
    const timer = setInterval(() => emit(clock.tick(elapsedMs())), TICK_MS);
    return () => clearInterval(timer);
  }, [enabled, elapsedMs, emit]);

  /** `useLiveGaze` 의 `onRecords` 에 넘깁니다 */
  const onGazeRecords = useCallback(
    (samples: GazeSampleRecord[]) => {
      const clock = clockRef.current;
      if (clock) emit(clock.gaze(samples));
    },
    [emit],
  );

  /**
   * 발표 끝. 안 보낸 시간을 발표 길이까지 메워 보냅니다 (마지막은 1초보다 짧을 수 있음).
   * `flushGaze` 다음, WS `stop` 전에 부릅니다 — 그래야 시선 마지막 조각이 실리고 `stop` 앞에 섭니다.
   */
  const finishMetrics = useCallback(
    (endMs: Ms) => {
      const clock = clockRef.current;
      if (clock) emit(clock.finish(endMs));
    },
    [emit],
  );

  /**
   * 탭이 사라지기 직전(`pagehide`). 시선을 기다리느라 아직 안 보낸 시간을 지금까지 메워 **보내지 않고**
   * 돌려줍니다 (`MetricsClock.flush`). 음량 1초 기록은 메모리에만 있어 페이지가 살아 있는 지금 붙여야 합니다.
   * 부르는 쪽이 소켓에 넘기고(`handOffMetrics`) 새로고침 뒤에 다시 보내도록 적어 둡니다.
   * `nowMs` 는 같은 순간 적는 무대 시계(`saveClock`)와 같은 값이라야 새 시계와 이어집니다.
   */
  const flushMetricsForHide = useCallback(
    (nowMs: Ms): MetricsMessage[] => {
      const clock = clockRef.current;
      return clock ? toMessages(clock.flush(nowMs)) : [];
    },
    [toMessages],
  );

  return { onGazeRecords, finishMetrics, flushMetricsForHide };
}
