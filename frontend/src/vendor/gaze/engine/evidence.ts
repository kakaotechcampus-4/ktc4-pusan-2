/**
 * Frame decisions -> 1 s gaze records (port of `gaze_lab/evidence/gaze.py`; parity-tested against it).
 *
 *     frame decisions --GazeSlicer--> GazeSample, one per slice (1 s by default)
 *
 * The 1 s record (`sampleToDict`) is what leaves the device: state, OTHER direction,
 * vote share, condition reliability and issues, frame count -- no image, no landmark.
 * Reading the records (coach issues, take summary, intervention outcome) is the
 * server's job (`gaze.core`); the demo page previews it with a copy in
 * `src/demo/evidence-preview.ts`, so the engine does not carry server logic.
 */
import type { EvidenceConfig } from './config';
import type { ConditionState } from './condition';
import type { FrameDecision } from './contract';
import { GAZE_DIRECTIONS, STATE_CLASSES, type GazeDirection, type StateClass } from './types';

export const UNCERTAIN = 'UNCERTAIN';
export const UNMEASURED = 'UNMEASURED';
export type SampleState = StateClass | typeof UNCERTAIN | typeof UNMEASURED;
/** Every slice state, decided ones first (also the tie-break order of a vote). */
export const SAMPLE_STATES: readonly SampleState[] = [...STATE_CLASSES, UNCERTAIN, UNMEASURED];

/** Round half up to 4 decimals -- the same arithmetic as the Python module. */
export function r4(value: number | null): number | null {
  return value === null ? null : Math.floor(value * 10000 + 0.5) / 10000;
}

// --------------------------------------------------------------------------
// Frames and slices
// --------------------------------------------------------------------------

export interface GazeFrame {
  t_ms: number;
  /** The decided state, or UNMEASURED when the frame had no usable face. */
  state: SampleState;
  direction: GazeDirection | null;
  reliability: number;
  issues: readonly string[];
}

export function frameFromDecision(
  d: FrameDecision,
  condition: ConditionState | null = d.condition,
): GazeFrame {
  const state: SampleState = d.face_valid ? d.state : UNMEASURED;
  return {
    t_ms: d.t_ms,
    state,
    direction: state === 'OTHER' ? d.direction : null,
    reliability: condition ? condition.reliability : 1,
    issues: condition ? [...condition.issues] : [],
  };
}

/** One slice of gaze evidence (the unit a timeline, and a wire record, is made of). */
export interface GazeSample {
  t_ms: number;
  duration_ms: number;
  state: SampleState;
  direction: GazeDirection | null;
  /** Share of the slice's usable frames that voted for `state` (0 when UNMEASURED). */
  confidence: number;
  /** Mean condition reliability over the slice's frames (0 for a slice without frames). */
  reliability: number;
  /** Condition issues present in at least half of the slice's frames. */
  issues: string[];
  frames: number;
}

/** The wire record (Python `GazeSample.to_dict()`). */
export function sampleToDict(s: GazeSample): GazeSample {
  return {
    ...s,
    confidence: r4(s.confidence)!,
    reliability: r4(s.reliability)!,
    issues: [...s.issues],
  };
}

function mode<T extends string>(values: readonly T[], order: readonly string[]): T | null {
  const counts = new Map<T, number>();
  for (const v of values) counts.set(v, (counts.get(v) ?? 0) + 1);
  let best: T | null = null;
  let bestKey: [number, number] = [-1, 0];
  for (const [v, n] of counts) {
    const idx = order.indexOf(v);
    const key: [number, number] = [n, idx >= 0 ? -idx : -order.length];
    if (key[0] > bestKey[0] || (key[0] === bestKey[0] && key[1] > bestKey[1])) {
      best = v;
      bestKey = key;
    }
  }
  return best;
}

/** One slice from its frames: majority vote, UNCERTAIN below the vote share, UNMEASURED when thin. */
export function decideSlice(
  frames: readonly GazeFrame[],
  start: number,
  duration: number,
  cfg: EvidenceConfig,
): GazeSample {
  const reliability = frames.length
    ? frames.reduce((s, f) => s + f.reliability, 0) / frames.length
    : 0;
  const seen = new Map<string, number>();
  for (const f of frames) for (const i of f.issues) seen.set(i, (seen.get(i) ?? 0) + 1);
  const issues = [...seen].filter(([, n]) => 2 * n >= frames.length).map(([i]) => i);
  const usable = frames.filter((f) => f.state !== UNMEASURED);
  const base = { t_ms: start, duration_ms: duration, reliability, issues, frames: frames.length };
  if (usable.length < cfg.min_frames_per_slice) {
    return { ...base, state: UNMEASURED, direction: null, confidence: 0 };
  }
  const winner = mode(
    usable.map((f) => f.state),
    SAMPLE_STATES,
  )!;
  const share = usable.filter((f) => f.state === winner).length / usable.length;
  if (share < cfg.slice_vote_threshold)
    return { ...base, state: UNCERTAIN, direction: null, confidence: share };
  const direction =
    winner === 'OTHER'
      ? mode(
          usable
            .filter((f) => f.state === 'OTHER' && f.direction)
            .map((f) => f.direction as GazeDirection),
          GAZE_DIRECTIONS,
        )
      : null;
  return { ...base, state: winner, direction, confidence: share };
}

/** Cuts frames into fixed slices on a grid anchored at the first frame; gaps become UNMEASURED slices. */
export class GazeSlicer {
  #start: number | null = null;
  #frames: GazeFrame[] = [];
  readonly cfg: EvidenceConfig;

  constructor(cfg: EvidenceConfig) {
    this.cfg = cfg;
  }

  push(frame: GazeFrame): GazeSample[] {
    const step = this.cfg.slice_ms;
    if (this.#start === null) this.#start = frame.t_ms;
    const out: GazeSample[] = [];
    while (frame.t_ms >= this.#start + step) {
      out.push(this.#close(this.#start, step));
      this.#start += step;
    }
    this.#frames.push(frame);
    return out;
  }

  flush(tEndMs: number | null = null): GazeSample[] {
    if (this.#start === null || this.#frames.length === 0) return [];
    const step = this.cfg.slice_ms;
    const end = tEndMs === null ? this.#start + step : tEndMs;
    const sample = this.#close(this.#start, Math.max(1, Math.min(step, end - this.#start)));
    this.#start += step;
    return [sample];
  }

  #close(start: number, duration: number): GazeSample {
    const frames = this.#frames;
    this.#frames = [];
    return decideSlice(frames, start, duration, this.cfg);
  }
}

// --------------------------------------------------------------------------
// Recorder
// --------------------------------------------------------------------------

/** Frame decisions in, 1 s records out.  Nothing is persisted here. */
export class GazeEvidenceRecorder {
  samples: GazeSample[] = [];
  #slicer: GazeSlicer;
  readonly cfg: EvidenceConfig;

  constructor(cfg: EvidenceConfig) {
    this.cfg = cfg;
    this.#slicer = new GazeSlicer(cfg);
  }

  /** Add one frame decision; returns the slices it completed. */
  record(decision: FrameDecision): GazeSample[] {
    const done = this.#slicer.push(frameFromDecision(decision));
    this.samples.push(...done);
    return done;
  }

  flush(tEndMs: number | null = null): GazeSample[] {
    const done = this.#slicer.flush(tEndMs);
    this.samples.push(...done);
    return done;
  }

  reset(): void {
    this.samples = [];
    this.#slicer = new GazeSlicer(this.cfg);
  }
}
