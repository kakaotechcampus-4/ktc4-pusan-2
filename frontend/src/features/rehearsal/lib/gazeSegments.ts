import type { GazeSegment, GazePayload, GazeZone, Ms } from '@/types/api';
import type { GazeSampleRecord } from '@/workers/gaze.contract';

/**
 * 엔진 상태 → FE 3구역. 종료 구간(`compressToSegments`)이 쓰는 형식이라 바꾸지 않습니다 (BE 약속).
 *
 *   CAMERA                          → CAMERA     청중
 *   SCREEN · BOTTOM                 → BOTTOM     화면 · 대본
 *   OTHER · UNCERTAIN · UNMEASURED  → UNCERTAIN  판정 불가 (OTHER 는 AI 기본값 `otherAs` 와 같습니다)
 *
 * 서버로 가는 1초 기록은 접지 않은 상태 그대로입니다. 테두리는 화면과 대본을 나눠 `borderOf` 가 정합니다.
 */
export function zoneOf(state: GazeSampleRecord['state']): GazeZone {
  if (state === 'CAMERA') return 'CAMERA';
  if (state === 'SCREEN' || state === 'BOTTOM') return 'BOTTOM';
  return 'UNCERTAIN';
}

/** 무대 테두리 상태 — `.stage` 의 `data-gaze` 값입니다 (`stage.css`) */
export type GazeBorder = 'AUDIENCE' | 'SCREEN' | 'SCRIPT' | 'UNKNOWN';

/**
 * 엔진 상태 → 무대 테두리. `zoneOf` 와 달리 **화면과 대본을 나눕니다** —
 * 리포트가 대본 응시 비율(`script_ratio`, 엔진의 `BOTTOM`)을 따로 보여 주므로 발표 중에도 따로 보입니다.
 *
 *   CAMERA                          → AUDIENCE  청중
 *   SCREEN                          → SCREEN    화면(슬라이드)
 *   BOTTOM                          → SCRIPT    대본 (화면 아래 대본 띠)
 *   OTHER · UNCERTAIN · UNMEASURED  → UNKNOWN   판정 불가
 */
export function borderOf(state: GazeSampleRecord['state']): GazeBorder {
  if (state === 'CAMERA') return 'AUDIENCE';
  if (state === 'SCREEN') return 'SCREEN';
  if (state === 'BOTTOM') return 'SCRIPT';
  return 'UNKNOWN';
}

/**
 * 1초 기록들을 같은 구역끼리 묶어 구간으로 압축합니다.
 * 서버로 보내는 것은 이 결과이고, 기록 하나하나가 아닙니다.
 *
 * 기록이 1초 주기라 10분 발표에 최대 600개.
 * (프레임 단위였다면 15fps × 600초 = 9,000개였습니다)
 *
 * 구간 길이는 기록마다의 `duration_ms` 를 씁니다. 앞 구간의 끝과 시작이 맞닿을 때만 잇습니다. 새로고침하면 1초 격자가 새로 시작해 틈이 생기는데,
 * 그 틈은 메우지 않고 구간을 끊습니다.
 */
export function compressToSegments(samples: readonly GazeSampleRecord[]): GazeSegment[] {
  if (samples.length === 0) return [];

  const out: GazeSegment[] = [];
  let cur: GazeSegment | null = null;
  let confSum = 0;
  let confCount = 0;

  for (const s of samples) {
    const zone = zoneOf(s.state);
    const endMs = s.t_ms + s.duration_ms;
    if (cur && cur.zone === zone && cur.endMs === s.t_ms) {
      cur.endMs = endMs;
      confSum += s.confidence;
      confCount++;
      cur.confidence = round2(confSum / confCount);
      continue;
    }
    if (cur) out.push(cur);
    confSum = s.confidence;
    confCount = 1;
    cur = { startMs: s.t_ms, endMs, zone, confidence: round2(s.confidence) };
  }
  if (cur) out.push(cur);
  return out;
}

/**
 * 구간에서 시간 합계를 냅니다.
 *
 * 관계식 — 서버의 422 검증과 같은 규칙입니다:
 *   measuredMs + uncertainMs === trackedMs
 *   cameraMs + bottomMs === measuredMs
 */
export function summarize(
  segments: GazeSegment[],
): Pick<GazePayload, 'trackedMs' | 'measuredMs' | 'uncertainMs'> & { cameraMs: Ms; bottomMs: Ms } {
  let cameraMs = 0;
  let bottomMs = 0;
  let uncertainMs = 0;

  for (const s of segments) {
    const len = s.endMs - s.startMs;
    if (s.zone === 'CAMERA') cameraMs += len;
    else if (s.zone === 'BOTTOM') bottomMs += len;
    else uncertainMs += len;
  }

  const measuredMs = cameraMs + bottomMs;
  return { trackedMs: measuredMs + uncertainMs, measuredMs, uncertainMs, cameraMs, bottomMs };
}

/**
 * 보내기 전에 스스로 검증합니다.
 * 서버가 422로 되돌려주기 전에 여기서 잡는 편이 낫습니다.
 */
export function validate(segments: GazeSegment[], durationMs: Ms): string[] {
  const errors: string[] = [];
  let prevEnd = -1;

  for (const s of segments) {
    if (s.startMs >= s.endMs) errors.push(`구간이 뒤집힘: ${s.startMs}~${s.endMs}`);
    if (s.startMs < prevEnd) errors.push(`구간이 겹침: ${s.startMs} < ${prevEnd}`);
    if (s.endMs > durationMs) errors.push(`발표 시간 초과: ${s.endMs} > ${durationMs}`);
    prevEnd = s.endMs;
  }

  const total = segments.reduce((a, s) => a + (s.endMs - s.startMs), 0);
  if (total > durationMs) errors.push(`구간 합이 발표 시간보다 김: ${total} > ${durationMs}`);

  return errors;
}

const round2 = (n: number) => Math.round(n * 100) / 100;
