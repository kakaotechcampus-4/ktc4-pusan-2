/**
 * WS `metrics` 를 어느 1초로 보낼지 정합니다 (`docs/ai/architecture.md` 1-4 · 6-1).
 *
 * `metrics` 하나는 1초 하나입니다 — 그 1초의 시선 1초 기록 · 음량 1초 기록 · 슬라이드 번호.
 * 1초 격자는 **시선 엔진의 첫 프레임**에서 시작합니다(6-1). 그래서 시선 기록이 오면 그 구간을 그대로
 * 씁니다. 그런데 시선이 없는 시간이 있습니다 — 엔진이 뜨기 전, 카메라 권한 거부 · 끊김, 보정이 없음.
 * 그동안에도 음량 · 슬라이드는 보내야 해서, 시선 기록이 오지 않으면 **스스로 1초씩 이어** 보냅니다
 * (`gaze: null`).
 *
 * ── 구간이 겹치지 않게 ──────────────────────────────────────────────
 * BE 는 1초 원자료를 `t_ms` 로 저장합니다. 두 구간이 겹치면 같은 시간의 음량을 두 번 셉니다.
 *   - 스스로 잇는 1초는 `GAZE_WAIT_MS` 를 기다린 뒤에만 보냅니다. 그 사이에 시선 기록이 오면 그걸 씁니다
 *   - 시선 기록이 이미 보낸 구간 뒤에서 시작하면, 그 사이는 꽉 찬 1초만 보내고 1초가 안 되는 자투리는
 *     버립니다. 엔진은 발표 전에 떠 있어서 자투리는 보통 시작 직후 0.1초 안팎이고, 보낸 기록이 없는
 *     시간은 BE 가 비어 있는 것으로 봅니다(6-2). 그래서 짧은 조각은 Take 끝에만 생깁니다
 *   - 이미 보낸 구간과 겹치는 시선 기록(아주 늦게 온 것)은 보내지 않습니다. 기록은 브라우저에 남습니다
 *
 * 시계와 React 를 모릅니다 — 시각을 받아 구간을 돌려줍니다. 그래서 브라우저 없이 테스트합니다.
 */
import type { Ms } from '@/types/api';
import type { GazeSampleRecord } from '@/workers/gaze.contract';

/** 1초 기록의 길이 (엔진의 묶는 단위와 같습니다) */
export const SLICE_MS = 1_000;

/**
 * 시선 기록을 이만큼 기다린 뒤에야 스스로 1초를 잇습니다. 시선 기록은 그 1초가 끝나고 다음 프레임이
 * 워커를 지나야 나와서 보통 0.1~0.3초 늦습니다. 넉넉히 두어 겹치는 일을 막습니다.
 */
export const GAZE_WAIT_MS = 2_000;

/** 보낼 1초 하나. 메시지는 이 구간으로 만듭니다 */
export interface MetricsWindow {
  startMs: Ms;
  endMs: Ms;
  /** 이 구간의 시선 1초 기록. 시선이 없었으면 null */
  gaze: GazeSampleRecord | null;
}

export interface MetricsClock {
  /** 새 시선 1초 기록 (시각 순). 보낼 구간을 돌려줍니다 */
  gaze(samples: readonly GazeSampleRecord[]): MetricsWindow[];
  /** 지금 시각. 시선 기록이 오지 않아 스스로 이어 보낼 구간을 돌려줍니다 */
  tick(nowMs: Ms): MetricsWindow[];
  /** 발표 끝. 아직 안 보낸 시간을 끝 시각까지 메워 돌려줍니다 (마지막은 1초보다 짧을 수 있음) */
  finish(endMs: Ms): MetricsWindow[];
  /**
   * 탭이 사라지기 직전(`pagehide`). 안 보낸 시간을 지금까지 메워 돌려줍니다 (마지막은 1초보다 짧을 수 있음).
   * `GAZE_WAIT_MS` 를 기다리지 않습니다 — 페이지가 사라지면 그 구간의 시선 기록은 더 오지 않습니다.
   * 새로고침하면 시계가 이 시각부터 다시 돌아서(`saveClock`) 다음 구간과 겹치지 않습니다.
   * `finish` 와 달리 닫지 않습니다 — 페이지가 되살아나면(bfcache) 이어서 보냅니다.
   */
  flush(nowMs: Ms): MetricsWindow[];
}

/** `[fromMs, toMs)` 안의 꽉 찬 1초들. 1초가 안 되는 끝 자투리는 내지 않습니다 */
function wholeSeconds(fromMs: Ms, toMs: Ms): MetricsWindow[] {
  const out: MetricsWindow[] = [];
  for (let start = fromMs; start + SLICE_MS <= toMs; start += SLICE_MS) {
    out.push({ startMs: start, endMs: start + SLICE_MS, gaze: null });
  }
  return out;
}

/** `[fromMs, toMs)` 를 1초씩 자릅니다. 마지막 조각은 짧을 수 있습니다 (Take 끝) */
function fill(fromMs: Ms, toMs: Ms): MetricsWindow[] {
  const out: MetricsWindow[] = [];
  for (let start = fromMs; start < toMs; start += SLICE_MS) {
    out.push({ startMs: start, endMs: Math.min(start + SLICE_MS, toMs), gaze: null });
  }
  return out;
}

/** @param startMs 보내기를 시작하는 무대 시각 (Take 처음이면 0, 새로고침해 이어받았으면 그 시각) */
export function createMetricsClock(startMs: Ms): MetricsClock {
  /** 다음에 보낼 구간의 시작. 여기까지는 이미 보냈습니다 */
  let next = startMs;
  let finished = false;

  return {
    gaze(samples) {
      if (finished) return [];
      const out: MetricsWindow[] = [];
      for (const s of samples) {
        const end = s.t_ms + s.duration_ms;
        // 이미 보낸 구간과 겹칩니다 — 같은 시간을 두 번 보내지 않습니다
        if (s.t_ms < next) continue;
        out.push(...wholeSeconds(next, s.t_ms), { startMs: s.t_ms, endMs: end, gaze: s });
        next = end;
      }
      return out;
    },

    tick(nowMs) {
      if (finished) return [];
      const out: MetricsWindow[] = [];
      while (next + SLICE_MS + GAZE_WAIT_MS <= nowMs) {
        out.push({ startMs: next, endMs: next + SLICE_MS, gaze: null });
        next += SLICE_MS;
      }
      return out;
    },

    finish(endMs) {
      if (finished) return [];
      finished = true;
      const out = fill(next, endMs);
      next = Math.max(next, endMs);
      return out;
    },

    flush(nowMs) {
      if (finished) return [];
      const out = fill(next, nowMs);
      next = Math.max(next, nowMs);
      return out;
    },
  };
}
