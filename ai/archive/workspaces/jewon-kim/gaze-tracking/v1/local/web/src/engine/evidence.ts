/**
 * Gaze evidence for the coach and review agents (port of
 * `vision/evidence/gaze.py`; parity-tested against it).
 *
 *     frame decisions --GazeSlicer--> GazeSample, one per slice (1 s by default)
 *                     --GazeTimeline--> evaluateGaze()        issues for the coach (now)
 *                                       takeSummary()         statistics + segments for the review
 *                                       interventionOutcome() before / after a coach feedback
 *                                       compareSummaries()    previous-take delta
 *
 * Issues use the agents' common evaluator output (`evaluator`, `issue_type`,
 * `severity`, `confidence`, `persistence_sec`, `evidence`, `actionable`, plus
 * `t_ms`).  Ratios divide by MEASURED time (CAMERA / SCREEN / BOTTOM / OTHER),
 * like the frontend's take statistics.  An OTHER slice carries the direction
 * the presenter looked (presenter-centric).
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

export const RUN_ISSUES = {
  BOTTOM: 'GAZE_ON_SCRIPT',
  SCREEN: 'GAZE_ON_SCREEN',
  OTHER: 'GAZE_AWAY',
} as const;
export const GAZE_LOW_EYE_CONTACT = 'GAZE_LOW_EYE_CONTACT';
export const GAZE_UNMEASURABLE = 'GAZE_UNMEASURABLE';
export type GazeIssueType =
  | (typeof RUN_ISSUES)[keyof typeof RUN_ISSUES]
  | typeof GAZE_LOW_EYE_CONTACT
  | typeof GAZE_UNMEASURABLE;

/** The coach action each gaze issue asks for; GAZE_UNMEASURABLE has none (no gaze feedback). */
export const GAZE_COACH_ACTION: Partial<Record<GazeIssueType, string>> = {
  GAZE_ON_SCRIPT: 'LOOK_AT_CAMERA',
  GAZE_ON_SCREEN: 'LOOK_AT_CAMERA',
  GAZE_AWAY: 'LOOK_AT_CAMERA',
  GAZE_LOW_EYE_CONTACT: 'LOOK_AT_CAMERA',
};

const OUTCOME_TARGET: Partial<Record<GazeIssueType, [StateClass, number]>> = {
  GAZE_ON_SCRIPT: ['BOTTOM', -1],
  GAZE_ON_SCREEN: ['SCREEN', -1],
  GAZE_AWAY: ['OTHER', -1],
  GAZE_LOW_EYE_CONTACT: ['CAMERA', 1],
};

/** Round half up to 4 decimals -- the same arithmetic as the Python module. */
export function r4(value: number | null): number | null {
  return value === null ? null : Math.floor(value * 10000 + 0.5) / 10000;
}

/** 5000 -> "5", 2500 -> "2.5" (window suffix in evidence keys). */
const secs = (ms: number): string => `${ms / 1000}`;
const clamp01 = (v: number): number => (v < 0 ? 0 : v > 1 ? 1 : v);

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

const endOf = (s: GazeSample): number => s.t_ms + s.duration_ms;

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
// Timeline
// --------------------------------------------------------------------------

export interface WindowStats {
  t_start_ms: number;
  t_end_ms: number;
  window_ms: number;
  tracked_ms: number;
  measured_ms: number;
  uncertain_ms: number;
  unmeasured_ms: number;
  coverage: number;
  state_ms: Record<SampleState, number>;
  ratio: Record<StateClass, number | null>;
  other_direction_ms: Record<GazeDirection, number>;
  issue_ms: Record<string, number>;
  mean_reliability: number;
  mean_confidence: number;
}

export interface Run {
  state: SampleState;
  start_ms: number;
  end_ms: number;
  duration_ms: number;
  direction: GazeDirection | null;
  mean_confidence: number;
  mean_reliability: number;
}

const runToDict = (r: Run) => ({
  state: r.state,
  start_ms: r.start_ms,
  end_ms: r.end_ms,
  duration_ms: r.duration_ms,
  direction: r.direction,
  mean_confidence: r4(r.mean_confidence),
  mean_reliability: r4(r.mean_reliability),
});

export class GazeTimeline {
  samples: GazeSample[];

  constructor(samples: readonly GazeSample[] = []) {
    this.samples = [...samples];
  }

  append(s: GazeSample): void {
    this.samples.push(s);
  }

  extend(samples: readonly GazeSample[]): void {
    this.samples.push(...samples);
  }

  get startMs(): number | null {
    return this.samples.length ? this.samples[0]!.t_ms : null;
  }

  get endMs(): number | null {
    return this.samples.length ? endOf(this.samples[this.samples.length - 1]!) : null;
  }

  /** The timeline as it stood at `tMs` (later slices dropped, the last one clipped). */
  until(tMs: number): GazeTimeline {
    const out: GazeSample[] = [];
    for (const s of this.samples) {
      if (s.t_ms >= tMs) break;
      out.push(endOf(s) > tMs ? { ...s, duration_ms: tMs - s.t_ms } : s);
    }
    return new GazeTimeline(out);
  }

  stats(tEndMs: number, windowMs: number): WindowStats {
    const t0 = tEndMs - windowMs;
    const stateMs = Object.fromEntries(SAMPLE_STATES.map((s) => [s, 0])) as Record<
      SampleState,
      number
    >;
    const dirMs = Object.fromEntries(GAZE_DIRECTIONS.map((d) => [d, 0])) as Record<
      GazeDirection,
      number
    >;
    const issueMs: Record<string, number> = {};
    let relSum = 0;
    let confSum = 0;
    for (const s of this.samples) {
      const overlap = Math.min(endOf(s), tEndMs) - Math.max(s.t_ms, t0);
      if (overlap <= 0) continue;
      stateMs[s.state] += overlap;
      relSum += s.reliability * overlap;
      if ((STATE_CLASSES as readonly string[]).includes(s.state)) confSum += s.confidence * overlap;
      if (s.state === 'OTHER' && s.direction) dirMs[s.direction] += overlap;
      for (const i of s.issues) issueMs[i] = (issueMs[i] ?? 0) + overlap;
    }
    const measured = STATE_CLASSES.reduce((a, s) => a + stateMs[s], 0);
    const tracked = SAMPLE_STATES.reduce((a, s) => a + stateMs[s], 0);
    return {
      t_start_ms: t0,
      t_end_ms: tEndMs,
      window_ms: windowMs,
      tracked_ms: tracked,
      measured_ms: measured,
      uncertain_ms: stateMs.UNCERTAIN,
      unmeasured_ms: stateMs.UNMEASURED,
      coverage: tracked ? measured / tracked : 0,
      state_ms: stateMs,
      ratio: Object.fromEntries(
        STATE_CLASSES.map((s) => [s, measured ? stateMs[s] / measured : null]),
      ) as Record<StateClass, number | null>,
      other_direction_ms: dirMs,
      issue_ms: issueMs,
      mean_reliability: tracked ? relSum / tracked : 0,
      mean_confidence: measured ? confSum / measured : 0,
    };
  }

  /** Consecutive same-state slices (OTHER merges across directions; a gap breaks a run). */
  runs(minMs = 0): Run[] {
    const out: Run[] = [];
    let current: GazeSample[] = [];
    const close = () => {
      if (!current.length) return;
      const total = current.reduce((a, s) => a + s.duration_ms, 0);
      let direction: GazeDirection | null = null;
      if (current[0]!.state === 'OTHER') {
        const weights = new Map<GazeDirection, number>();
        for (const s of current)
          if (s.direction)
            weights.set(s.direction, (weights.get(s.direction) ?? 0) + s.duration_ms);
        let bestKey: [number, number] | null = null;
        for (const [d, w] of weights) {
          const key: [number, number] = [w, -GAZE_DIRECTIONS.indexOf(d)];
          if (!bestKey || key[0] > bestKey[0] || (key[0] === bestKey[0] && key[1] > bestKey[1])) {
            direction = d;
            bestKey = key;
          }
        }
      }
      const run: Run = {
        state: current[0]!.state,
        start_ms: current[0]!.t_ms,
        end_ms: endOf(current[current.length - 1]!),
        duration_ms: 0,
        direction,
        mean_confidence: total
          ? current.reduce((a, s) => a + s.confidence * s.duration_ms, 0) / total
          : 0,
        mean_reliability: total
          ? current.reduce((a, s) => a + s.reliability * s.duration_ms, 0) / total
          : 0,
      };
      run.duration_ms = run.end_ms - run.start_ms;
      if (run.duration_ms >= minMs) out.push(run);
    };
    for (const s of this.samples) {
      const last = current[current.length - 1];
      if (last && (s.state !== last.state || s.t_ms !== endOf(last))) {
        close();
        current = [];
      }
      current.push(s);
    }
    close();
    return out;
  }
}

// --------------------------------------------------------------------------
// Coach: issues now
// --------------------------------------------------------------------------

export interface GazeIssue {
  evaluator: 'gaze';
  issue_type: GazeIssueType;
  t_ms: number;
  severity: number;
  confidence: number;
  persistence_sec: number;
  evidence: Record<string, unknown>;
  actionable: boolean;
}

function issue(
  issueType: GazeIssueType,
  tMs: number,
  severity: number,
  confidence: number,
  persistenceMs: number,
  evidence: Record<string, unknown>,
  actionable: boolean,
): GazeIssue {
  return {
    evaluator: 'gaze',
    issue_type: issueType,
    t_ms: tMs,
    severity: r4(clamp01(severity))!,
    confidence: r4(clamp01(confidence))!,
    persistence_sec: Math.floor(persistenceMs / 10 + 0.5) / 100,
    evidence,
    actionable,
  };
}

/** Gaze issues at `tMs`, most severe first, in the agents' evaluator format. */
export function evaluateGaze(
  timeline: GazeTimeline,
  tMs: number,
  cfg: EvidenceConfig,
): GazeIssue[] {
  const tl = timeline.until(tMs);
  if (!tl.samples.length) return [];
  const shortS = secs(cfg.short_window_ms);
  const longS = secs(cfg.long_window_ms);
  const short = tl.stats(tMs, cfg.short_window_ms);
  const long = tl.stats(tMs, cfg.long_window_ms);
  const out: GazeIssue[] = [];

  const runs = tl.runs();
  const run = runs[runs.length - 1];
  const limits: Record<string, [number, number]> = {
    BOTTOM: [cfg.script_min_ms, cfg.script_full_ms],
    SCREEN: [cfg.screen_min_ms, cfg.screen_full_ms],
    OTHER: [cfg.away_min_ms, cfg.away_full_ms],
  };
  if (run && run.state in RUN_ISSUES && run.duration_ms >= limits[run.state]![0]) {
    const state = run.state as keyof typeof RUN_ISSUES;
    const key = state.toLowerCase();
    const evidence: Record<string, unknown> = {
      state,
      run_start_ms: run.start_ms,
      continuous_ms: run.duration_ms,
      [`${key}_ratio_${shortS}s`]: r4(short.ratio[state]),
      [`${key}_ratio_${longS}s`]: r4(long.ratio[state]),
      [`camera_ratio_${longS}s`]: r4(long.ratio.CAMERA),
      mean_reliability: r4(run.mean_reliability),
    };
    if (state === 'OTHER') evidence.direction = run.direction;
    out.push(
      issue(
        RUN_ISSUES[state],
        tMs,
        run.duration_ms / limits[state]![1],
        run.mean_confidence * run.mean_reliability,
        run.duration_ms,
        evidence,
        true,
      ),
    );
  }

  const camera = long.ratio.CAMERA;
  const threshold = cfg.low_eye_contact_ratio;
  if (
    long.measured_ms >= cfg.low_eye_contact_min_measured_ms &&
    camera !== null &&
    camera < threshold
  ) {
    out.push(
      issue(
        GAZE_LOW_EYE_CONTACT,
        tMs,
        threshold > 0 ? (threshold - camera) / threshold : 0,
        long.mean_confidence * long.mean_reliability,
        long.measured_ms,
        {
          [`camera_ratio_${longS}s`]: r4(camera),
          [`screen_ratio_${longS}s`]: r4(long.ratio.SCREEN),
          [`bottom_ratio_${longS}s`]: r4(long.ratio.BOTTOM),
          [`other_ratio_${longS}s`]: r4(long.ratio.OTHER),
          measured_ms: long.measured_ms,
        },
        true,
      ),
    );
  }

  const covThr = cfg.unmeasurable_coverage;
  const relThr = cfg.unmeasurable_reliability;
  if (short.tracked_ms > 0 && (short.coverage < covThr || short.mean_reliability < relThr)) {
    const worst = Math.min(
      covThr > 0 ? short.coverage / covThr : 1,
      relThr > 0 ? short.mean_reliability / relThr : 1,
    );
    out.push(
      issue(
        GAZE_UNMEASURABLE,
        tMs,
        1 - worst,
        1,
        short.tracked_ms - short.measured_ms,
        {
          [`coverage_${shortS}s`]: r4(short.coverage),
          [`mean_reliability_${shortS}s`]: r4(short.mean_reliability),
          unmeasured_ms: short.unmeasured_ms,
          uncertain_ms: short.uncertain_ms,
          condition_issue_ms: { ...short.issue_ms },
        },
        false,
      ),
    );
  }

  return out
    .map((v, i) => [v, i] as const)
    .sort((a, b) => b[0].severity - a[0].severity || a[1] - b[1])
    .map(([v]) => v);
}

// --------------------------------------------------------------------------
// Review: the whole take
// --------------------------------------------------------------------------

export function takeSummary(timeline: GazeTimeline, cfg: EvidenceConfig): Record<string, unknown> {
  if (!timeline.samples.length) {
    return { evaluator: 'gaze', tracked_ms: 0, measured_ms: 0, segments: [], problem_segments: [] };
  }
  const start = timeline.startMs!;
  const end = timeline.endMs!;
  const total = timeline.stats(end, end - start);
  const runs = timeline.runs();
  const limits: Record<string, number> = {
    BOTTOM: cfg.script_min_ms,
    SCREEN: cfg.screen_min_ms,
    OTHER: cfg.away_min_ms,
  };
  const segments = runs.filter((r) => r.duration_ms >= cfg.segment_min_ms).map(runToDict);
  const problems = runs
    .filter((r) => r.state in RUN_ISSUES && r.duration_ms >= limits[r.state]!)
    .map((r) => ({ ...runToDict(r), issue_type: RUN_ISSUES[r.state as keyof typeof RUN_ISSUES] }));
  const longest = Object.fromEntries(
    STATE_CLASSES.map((s) => [
      s,
      Math.max(0, ...runs.filter((r) => r.state === s).map((r) => r.duration_ms)),
    ]),
  );
  const episodes = Object.fromEntries(
    STATE_CLASSES.map((s) => [
      s,
      runs.filter((r) => r.state === s && r.duration_ms >= cfg.segment_min_ms).length,
    ]),
  );
  return {
    evaluator: 'gaze',
    start_ms: start,
    end_ms: end,
    tracked_ms: total.tracked_ms,
    measured_ms: total.measured_ms,
    uncertain_ms: total.uncertain_ms,
    unmeasured_ms: total.unmeasured_ms,
    coverage: r4(total.coverage),
    state_ms: { ...total.state_ms },
    state_ratio: Object.fromEntries(STATE_CLASSES.map((s) => [s, r4(total.ratio[s])])),
    eye_contact_ratio: r4(total.ratio.CAMERA),
    other_direction_ms: { ...total.other_direction_ms },
    mean_reliability: r4(total.mean_reliability),
    condition_issue_ms: { ...total.issue_ms },
    longest_run_ms: longest,
    episodes,
    segments,
    problem_segments: problems,
  };
}

type Summary = Record<string, unknown>;
const field = (s: Summary, k: string): number | null =>
  typeof s[k] === 'number' ? (s[k] as number) : null;
const nested = (s: Summary, k: string, sub: string): number | null => {
  const v = (s[k] as Record<string, unknown> | undefined)?.[sub];
  return typeof v === 'number' ? v : null;
};

/** Previous-take delta: the same numbers side by side, `delta = current - previous`. */
export function compareSummaries(previous: Summary, current: Summary): Record<string, unknown> {
  const pair = (a: number | null, b: number | null) => ({
    previous: a,
    current: b,
    delta: a === null || b === null ? null : r4(b - a),
  });
  const per = (k: string) =>
    Object.fromEntries(
      STATE_CLASSES.map((s) => [s, pair(nested(previous, k, s), nested(current, k, s))]),
    );
  return {
    eye_contact_ratio: pair(
      field(previous, 'eye_contact_ratio'),
      field(current, 'eye_contact_ratio'),
    ),
    coverage: pair(field(previous, 'coverage'), field(current, 'coverage')),
    mean_reliability: pair(field(previous, 'mean_reliability'), field(current, 'mean_reliability')),
    state_ratio: per('state_ratio'),
    episodes: per('episodes'),
    longest_run_ms: per('longest_run_ms'),
  };
}

/** Did the presenter change after a coach feedback for `issueType` at `tMs`? */
export function interventionOutcome(
  timeline: GazeTimeline,
  tMs: number,
  issueType: GazeIssueType,
  cfg: EvidenceConfig,
): Record<string, unknown> {
  const target = OUTCOME_TARGET[issueType];
  if (!target) throw new Error(`no outcome rule for ${issueType}`);
  const [state, sign] = target;
  const before = timeline.stats(tMs, cfg.outcome_before_ms);
  const after = timeline.stats(
    tMs + cfg.outcome_delay_ms + cfg.outcome_after_ms,
    cfg.outcome_after_ms,
  );
  const rb = before.ratio[state];
  const ra = after.ratio[state];
  const key = state.toLowerCase();
  return {
    intervention: { type: GAZE_COACH_ACTION[issueType], issue_type: issueType, t_ms: tMs },
    before: {
      [`${key}_ratio_${secs(cfg.outcome_before_ms)}s`]: r4(rb),
      measured_ms: before.measured_ms,
    },
    [`after_${secs(cfg.outcome_delay_ms)}s`]: {
      [`${key}_ratio_${secs(cfg.outcome_after_ms)}s`]: r4(ra),
      measured_ms: after.measured_ms,
    },
    effective: rb === null || ra === null ? null : (ra - rb) * sign >= cfg.outcome_min_change,
  };
}

// --------------------------------------------------------------------------
// Recorder
// --------------------------------------------------------------------------

/** Frame decisions in, slices and a timeline out.  Nothing is persisted here. */
export class GazeEvidenceRecorder {
  timeline = new GazeTimeline();
  #slicer: GazeSlicer;
  readonly cfg: EvidenceConfig;

  constructor(cfg: EvidenceConfig) {
    this.cfg = cfg;
    this.#slicer = new GazeSlicer(cfg);
  }

  /** Add one frame decision; returns the slices it completed. */
  record(decision: FrameDecision): GazeSample[] {
    const done = this.#slicer.push(frameFromDecision(decision));
    this.timeline.extend(done);
    return done;
  }

  flush(tEndMs: number | null = null): GazeSample[] {
    const done = this.#slicer.flush(tEndMs);
    this.timeline.extend(done);
    return done;
  }

  issues(tMs: number | null = null): GazeIssue[] {
    const end = this.timeline.endMs;
    return end === null ? [] : evaluateGaze(this.timeline, tMs ?? end, this.cfg);
  }

  summary(): Record<string, unknown> {
    return takeSummary(this.timeline, this.cfg);
  }

  reset(): void {
    this.timeline = new GazeTimeline();
    this.#slicer = new GazeSlicer(this.cfg);
  }
}
