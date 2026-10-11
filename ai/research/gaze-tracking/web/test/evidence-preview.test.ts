/**
 * The demo page's preview of the server core (`src/demo/evidence-preview.ts`):
 *
 * - against the Python core's answers (`test/fixtures/evidence.json`, written by
 *   tools/make_fixtures.py): runs, window stats, coach issues, take summary,
 *   previous-take delta and intervention outcome, keys included;
 * - end to end: engine decisions -> 1 s records -> issues and summary.
 *
 * The 1 s records themselves are the engine's and are tested next to it
 * (src/engine/__tests__/parity.test.ts).
 */
import { describe, expect, it } from 'vitest';
import {
  compareSummaries,
  evaluateGaze,
  GazeTimeline,
  interventionOutcome,
  issuesNow,
  takeSummary,
  type GazeIssueType,
} from '../src/demo/evidence-preview';
import { makeConfig } from '../src/engine/config';
import { GazeEngine } from '../src/engine/engine';
import {
  GazeEvidenceRecorder,
  GazeSlicer,
  r4,
  type GazeFrame,
  type GazeSample,
} from '../src/engine/evidence';
import {
  close,
  FakeDetector,
  frame,
  revive,
  type FakeFrame,
} from '../src/engine/__tests__/helpers';
import fixture from './fixtures/evidence.json';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>;
const rows = (v: unknown): Row[] => v as Row[];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const FX: any = revive(fixture);
const CFG = makeConfig();

function sameDict(actual: unknown, expected: unknown, path = '$'): void {
  if (expected && typeof expected === 'object' && !Array.isArray(expected)) {
    const a = actual as Record<string, unknown>;
    expect(Object.keys(a).sort(), path).toEqual(Object.keys(expected).sort());
    for (const [k, v] of Object.entries(expected)) sameDict(a[k], v, `${path}.${k}`);
    return;
  }
  if (Array.isArray(expected)) {
    expect((actual as unknown[]).length, path).toBe(expected.length);
    expected.forEach((v, i) => sameDict((actual as unknown[])[i], v, `${path}[${i}]`));
    return;
  }
  close(actual, expected, 1e-12, path);
}

describe('preview of the server core (same answers as gaze/core.py)', () => {
  const ev = FX.evidence;
  const cfg = CFG.evidence;
  const toFrame = (d: Row): GazeFrame => ({
    t_ms: d.t_ms,
    state: d.state,
    direction: d.direction ?? null,
    reliability: d.reliability,
    issues: d.issues ?? [],
  });
  const slice = (frames: Row[], tEnd: number): GazeSample[] => {
    const slicer = new GazeSlicer(cfg);
    const out = frames.flatMap((d) => slicer.push(toFrame(d)));
    return [...out, ...slicer.flush(tEnd)];
  };
  const samples = slice(ev.frames, ev.t_end);
  const tl = new GazeTimeline(samples);

  it('runs', () => {
    sameDict(
      tl.runs().map((r) => ({
        ...r,
        mean_confidence: r4(r.mean_confidence),
        mean_reliability: r4(r.mean_reliability),
      })),
      ev.runs,
    );
  });

  it.each(rows(ev.stats))('window stats at $t over $window ms', (c) => {
    sameDict(tl.stats(c.t, c.window), c.out);
  });

  it.each(rows(ev.issues))('coach issues at $t', (c) => {
    sameDict(evaluateGaze(tl, c.t, cfg), c.out);
  });

  it('review summary and previous-take delta', () => {
    sameDict(takeSummary(tl, cfg), ev.summary);
    const take2 = slice(ev.take2.frames, ev.take2.frames.at(-1).t_ms + 125);
    const summary2 = takeSummary(new GazeTimeline(take2), cfg);
    sameDict(summary2, ev.take2.summary);
    sameDict(compareSummaries(summary2, takeSummary(tl, cfg)), ev.compare);
    sameDict(
      compareSummaries(takeSummary(new GazeTimeline(), cfg), takeSummary(tl, cfg)),
      ev.compare_empty,
    );
  });

  it.each(rows(ev.outcomes))('intervention outcome: $issue at $t', (c) => {
    sameDict(interventionOutcome(tl, c.t, c.issue as GazeIssueType, cfg), c.out);
  });
});

describe('end to end: engine decisions -> 1 s records -> preview', () => {
  const LENS = 18;
  const SCREEN = 11;
  const SCRIPT = 4;
  const look = (pitch: number, yaw = 0.5, n = 16): FakeFrame[] =>
    Array.from({ length: n }, (_, i) =>
      frame(yaw + ((i % 3) - 1) * 0.2, pitch + ((i % 5) - 2) * 0.15),
    );

  it('turns frame decisions into the issues and summary the coach and review agents read', () => {
    const e = new GazeEngine<FakeFrame>({
      detector: new FakeDetector(),
      luma: () => ({ measure: () => ({ face: 120, background: 100 }) }),
    });
    e.checkPlacement(look(LENS), look(SCREEN));
    e.fitCalibration(look(LENS), look(SCRIPT));
    const cfg = makeConfig().evidence;
    const rec = new GazeEvidenceRecorder(cfg);
    let t = 800_000;
    const feed = (pitch: number, yaw: number, seconds: number) => {
      for (let i = 0; i < seconds * 8; i++) rec.record(e.classify(frame(yaw, pitch), (t += 125)));
    };
    feed(LENS, 0.5, 4);
    feed(SCRIPT, 0.5, 5);
    const script = issuesNow(rec.samples, cfg);
    expect(script[0]).toMatchObject({
      evaluator: 'gaze',
      issue_type: 'GAZE_ON_SCRIPT',
      actionable: true,
    });
    expect(script[0]!.persistence_sec).toBeGreaterThanOrEqual(4);
    feed(LENS, -50, 3);
    const away = issuesNow(rec.samples, cfg).find((i) => i.issue_type === 'GAZE_AWAY')!;
    expect(away.evidence.direction).toBe('RIGHT');
    rec.flush(t + 125);
    const summary = takeSummary(new GazeTimeline(rec.samples), cfg) as {
      problem_segments: { issue_type: string }[];
    };
    expect(summary.problem_segments.map((p) => p.issue_type)).toEqual([
      'GAZE_ON_SCRIPT',
      'GAZE_AWAY',
    ]);
  });
});
