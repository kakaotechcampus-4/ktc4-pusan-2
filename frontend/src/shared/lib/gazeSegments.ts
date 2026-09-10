import type { GazeSegment, GazePayload, Ms } from '@/types/api';
import type { ZoneDecision } from '@/workers/gaze.contract';

/**
 * 1초 판정들을 같은 zone끼리 묶어 구간으로 압축합니다.
 * 서버로 보내는 것은 이 결과이고, 판정 하나하나가 아닙니다.
 *
 * 판정이 1초 주기라 10분 발표에 최대 600개.
 * (프레임 단위였다면 15fps × 600초 = 9,000개였습니다)
 */
export function compressToSegments(decisions: ZoneDecision[], intervalMs: Ms): GazeSegment[] {
  if (decisions.length === 0) return [];

  const out: GazeSegment[] = [];
  let cur: GazeSegment | null = null;
  let confSum = 0;
  let confCount = 0;

  for (const d of decisions) {
    if (cur && cur.zone === d.zone && cur.endMs === d.tMs) {
      cur.endMs = d.tMs + intervalMs;
      confSum += d.confidence;
      confCount++;
      cur.confidence = round2(confSum / confCount);
      continue;
    }
    if (cur) out.push(cur);
    confSum = d.confidence;
    confCount = 1;
    cur = {
      startMs: d.tMs,
      endMs: d.tMs + intervalMs,
      zone: d.zone,
      confidence: round2(d.confidence),
    };
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
