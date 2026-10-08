/**
 * Parity with the Python engine: every expectation below was produced by the
 * real Python code (`tools/make_fixtures.py`), never typed by hand.  When a
 * test fails, either the port drifted or the fixtures are stale -- regenerate
 * them first (`npm run fixtures`) and look again.
 */
import { describe, expect, it } from 'vitest';
import { ConditionMonitor, SceneBaselineAccumulator } from '../condition';
import { CONFIG_HASH, makeConfig } from '../config';
import { CalibrationGauge } from '../gauge';
import {
  bboxFromLandmarks,
  eyeAspectRatio,
  inBoundsFraction,
  irisDiameterPx,
  secondFaceRatio,
  selectMainFace,
} from '../geometry';
import { headPoseFromMatrix } from '../headpose';
import { logNdtr } from '../math';
import { placementFromSamples } from '../placement';
import { PreconditionChecker } from '../preconditions';
import { HeadSweep } from '../sweep';
import { GazeSlicer, sampleToDict, type GazeFrame, type GazeSample } from '../evidence';
import {
  decideFrame,
  directionOf,
  fitReference,
  gazeOffset,
  aimOffset,
  softBoxLogDensity,
} from '../reference';
import type { Bbox, Cue, Sample } from '../types';
import { close, FX, makeObs, type ObsSpec } from './helpers';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>;
const rows = (v: unknown): Row[] => v as Row[];

const CFG = makeConfig();

it('the fixtures come from the same Python config as the engine defaults', () => {
  expect(FX.config_hash).toBe(CONFIG_HASH);
});

describe('math', () => {
  it.each(FX.math.log_ndtr as { x: number; out: number }[])('logNdtr($x)', ({ x, out }) => {
    close(logNdtr(x), out, 1e-12);
  });

  it.each(FX.math.soft_box as { x: number; lo: number; hi: number; s: number; out: number }[])(
    'softBoxLogDensity($x, [$lo, $hi], $s)',
    ({ x, lo, hi, s, out }) => close(softBoxLogDensity(x, lo, hi, s), out, 1e-11),
  );
});

describe('head pose from the transformation matrix', () => {
  it.each(rows(FX.headpose).map((c, i): Row => ({ ...c, i })))(
    'case $i, both memory layouts',
    (c) => {
      for (const data of [c.col_major, c.row_major]) {
        const pose = headPoseFromMatrix(data);
        close([pose.yaw, pose.pitch, pose.roll], [c.yaw, c.pitch, c.roll], 1e-12);
        close(pose.depthCm, c.depth_cm, 1e-9);
      }
    },
  );
});

describe('landmark geometry', () => {
  const size = FX.geometry.size as [number, number];
  const faces = FX.geometry.faces.map((f: { landmarks: number[][] }) => ({
    ...f,
    lm: f.landmarks.map(([x, y, z]) => ({ x: x!, y: y!, z })),
  }));

  it.each(rows(faces).map((f, i) => ({ f, i })))('face $i', ({ f }) => {
    expect(bboxFromLandmarks(f.lm, size)).toEqual(f.bbox);
    close(eyeAspectRatio(f.lm, 'left', size), f.ear_left, 1e-12);
    close(eyeAspectRatio(f.lm, 'right', size), f.ear_right, 1e-12);
    close(irisDiameterPx(f.lm, size), f.iris, 1e-12);
    close(inBoundsFraction(f.lm), f.visibility, 1e-15);
  });

  it('main face selection, with and without a hint', () => {
    const bboxes = faces.map((f: { bbox: Bbox }) => f.bbox);
    for (const { hint, out } of FX.geometry.select)
      expect(selectMainFace(bboxes, size, hint)).toBe(out);
  });

  it('only a distinct face counts as someone else (not the main face twice, not a tiny find)', () => {
    for (const { main, others, out } of FX.geometry.second)
      close(secondFaceRatio(main, others, size, 0.01), out, 1e-12);
  });
});

function samplesOf(
  rows: { cue: Cue; yaw: number; pitch: number; head_yaw: number; head_pitch: number }[],
): Sample[] {
  return rows.map((r) => ({
    cue: r.cue,
    yawDeg: r.yaw,
    pitchDeg: r.pitch,
    headYawDeg: r.head_yaw,
    headPitchDeg: r.head_pitch,
  }));
}

describe('reference classifier', () => {
  it.each(rows(FX.reference))('$name', (c) => {
    const cfg = { ...CFG.calibration, ...c.overrides };
    const { model, quality } = fitReference(samplesOf(c.samples), cfg);

    const q = c.quality;
    expect(quality.status).toBe(q.status);
    expect(quality.reason).toBe(q.reason);
    expect(quality.hint).toBe(q.hint);
    expect(quality.warnings).toEqual(q.warnings);
    expect([quality.n_camera, quality.n_bottom, quality.n_screen]).toEqual([
      q.n_camera,
      q.n_bottom,
      q.n_screen,
    ]);
    close(quality.loo_accuracy, q.loo_accuracy, 1e-12);
    close(
      {
        separability: quality.separability,
        centroid_distance: quality.centroid_distance,
        camera_variance: quality.camera_variance,
        bottom_variance: quality.bottom_variance,
        anchors_deg: quality.anchors_deg,
        sigma_deg: quality.sigma_deg,
        pair_separation: quality.pair_separation,
        camera_capture_radius_deg: quality.camera_capture_radius_deg,
        camera_centroid: quality.camera_centroid,
        bottom_centroid: quality.bottom_centroid,
      },
      {
        separability: q.separability,
        centroid_distance: q.centroid_distance,
        camera_variance: q.camera_variance,
        bottom_variance: q.bottom_variance,
        anchors_deg: q.anchors_deg,
        sigma_deg: q.sigma_deg,
        pair_separation: q.pair_separation,
        camera_capture_radius_deg: q.camera_capture_radius_deg,
        camera_centroid: q.camera_centroid,
        bottom_centroid: q.bottom_centroid,
      },
      1e-9,
    );

    if (c.model === null) return void expect(model).toBeNull();
    expect(model).not.toBeNull();
    const m = c.model;
    expect(model!.classes).toEqual(m.classes);
    expect(model!.counts).toEqual(m.counts);
    close(model!.anchors, m.anchors, 1e-12);
    close(model!.sigma, m.sigma, 1e-12);
    close(model!.spreads, m.spreads, 1e-12);
    close(model!.logPriors, m.log_priors, 1e-12);
    close(model!.logOtherDensity, m.log_other_density, 1e-12);
    close(model!.headBaseline, m.head_baseline, 1e-12);
    for (const [cue, b] of Object.entries(m.boxes as Record<string, number[]>)) {
      const box = model!.boxes[cue as Cue]!;
      close([box.yawLo, box.yawHi, box.pitchLo, box.pitchHi], b, 1e-12);
    }
    for (const d of c.decisions) {
      const [yaw, pitch, hy, hp] = d.query;
      const out = decideFrame(model!, yaw, pitch, hy, hp, cfg);
      expect(out.state, `query ${d.query}`).toBe(d.label);
      expect(out.reason, `query ${d.query}`).toBe(d.reason);
      close(out.probs, d.probs, 1e-9, `probs ${d.query}`);
      const offset = gazeOffset(model!, yaw, pitch, cfg.screen_min_halfwidth_deg);
      close(offset, d.offset_deg, 1e-9, `offset ${d.query}`);
      close(aimOffset(model!, yaw, pitch), d.aim_deg, 1e-9, `aim ${d.query}`);
      expect(out.state === 'OTHER' ? directionOf(offset) : null, `direction ${d.query}`).toBe(
        d.direction ?? null,
      );
    }
  });
});

describe('camera placement', () => {
  it.each(rows(FX.placement))('$name', (c) => {
    const out = placementFromSamples(c.camera, c.screen, CFG.calibration, CFG.placement)!;
    const e = c.out;
    expect([out.placement, out.supported, out.reason, out.hint, out.axis]).toEqual([
      e.placement,
      e.supported,
      e.reason,
      e.hint,
      e.axis,
    ]);
    close(
      [
        out.delta_pitch_deg,
        out.delta_yaw_deg,
        out.separation,
        out.axis_dominance,
        out.offset_deg,
        out.n_camera,
        out.n_screen,
      ],
      [
        e.delta_pitch_deg,
        e.delta_yaw_deg,
        e.separation,
        e.axis_dominance,
        e.offset_deg,
        e.n_camera,
        e.n_screen,
      ],
      1e-12,
    );
    close(out.camera_centroid_deg, e.camera_centroid_deg, 1e-12);
    close(out.screen_centroid_deg, e.screen_centroid_deg, 1e-12);
  });
});

describe('sequences', () => {
  it('set-up check: PASS / RETRY / REJECT timing and every reason', () => {
    const checker = new PreconditionChecker(CFG.preconditions);
    const frames = FX.preconditions.frames as ObsSpec[];
    frames.forEach((f, i) => {
      const r = checker.update(makeObs(f));
      const e = FX.preconditions.reports[i];
      expect([r.status, r.reason, r.held_ms, r.failing_ms, r.blocking], `frame ${i}`).toEqual([
        e.status,
        e.reason,
        e.held_ms,
        e.failing_ms,
        e.blocking,
      ]);
      expect(
        r.checks.map((c) => [c.name, c.ok]),
        `frame ${i}`,
      ).toEqual(e.checks);
      close(r.measurements, e.measurements, 1e-9, `frame ${i}`);
    });
  });

  it('live conditions: baseline, reliability ramps, severe states, emission', () => {
    const acc = new SceneBaselineAccumulator();
    for (const c of FX.condition.calibration) acc.add(makeObs(c.frame), c.cue);
    const base = acc.build()!;
    const eb = FX.condition.baseline;
    close(
      {
        centre: base.centre,
        faceArea: base.faceArea,
        irisPx: base.irisPx,
        brightness: base.brightness,
        head: base.head,
        headPoses: base.headPoses,
        depthCm: base.depthCm,
      },
      {
        centre: eb.centre,
        faceArea: eb.face_area,
        irisPx: eb.iris_px,
        brightness: eb.brightness,
        head: eb.head,
        headPoses: eb.head_poses,
        depthCm: eb.depth_cm,
      },
      1e-12,
    );
    const monitor = new ConditionMonitor(CFG.condition, base);
    (FX.condition.live as ObsSpec[]).forEach((f, i) => {
      const s = monitor.update(makeObs(f));
      const e = FX.condition.states[i];
      expect([s.issues, s.severe], `frame ${i}`).toEqual([e.issues, e.severe]);
      close(s.reliability, e.reliability, 1e-12, `frame ${i}`);
      close(s.components, e.components, 1e-12, `frame ${i}`);
      expect(Object.keys(s.components).sort(), `frame ${i}`).toEqual(
        Object.keys(e.components).sort(),
      );
      expect(monitor.shouldEmit(s), `frame ${i}`).toBe(e.emit);
    });
  });

  it('head-direction jitter: steady, a smooth turn is not noise, shaky, a gap, steady again', () => {
    const acc = new SceneBaselineAccumulator();
    for (const c of FX.condition_jitter.calibration) acc.add(makeObs(c.frame), c.cue);
    const monitor = new ConditionMonitor(CFG.condition, acc.build()!, true);
    (FX.condition_jitter.live as ObsSpec[]).forEach((f, i) => {
      const s = monitor.update(makeObs(f));
      const e = FX.condition_jitter.states[i];
      expect(s.issues, `frame ${i}`).toEqual(e.issues);
      close(s.reliability, e.reliability, 1e-12, `frame ${i}`);
      close(s.components, e.components, 1e-12, `frame ${i}`);
      close(s.jitter_deg, e.jitter_deg, 1e-9, `frame ${i} jitter`);
    });
  });

  it('drift from the face-mesh distance: hidden eyes are no move, iris only as fallback', () => {
    const acc = new SceneBaselineAccumulator();
    for (const c of FX.condition_depth.calibration) acc.add(makeObs(c.frame), c.cue);
    const base = acc.build()!;
    close(base.depthCm, FX.condition_depth.depth_cm, 1e-12);
    const monitor = new ConditionMonitor(CFG.condition, base, true);
    (FX.condition_depth.live as ObsSpec[]).forEach((f, i) => {
      const s = monitor.update(makeObs(f));
      const e = FX.condition_depth.states[i];
      expect([s.issues, s.severe], `frame ${i}`).toEqual([e.issues, e.severe]);
      close(s.reliability, e.reliability, 1e-12, `frame ${i}`);
      close(s.drift, e.drift, 1e-9, `frame ${i} drift`);
    });
  });

  it('live conditions with the head-pose backbone: a turned head is not a condition', () => {
    const acc = new SceneBaselineAccumulator();
    for (const c of FX.condition.calibration) acc.add(makeObs(c.frame), c.cue);
    const monitor = new ConditionMonitor(CFG.condition, acc.build()!, true);
    (FX.condition.live as ObsSpec[]).forEach((f, i) => {
      const s = monitor.update(makeObs(f));
      const e = FX.condition.states_head_is_gaze[i];
      expect([s.issues, s.severe], `frame ${i}`).toEqual([e.issues, e.severe]);
      close(s.reliability, e.reliability, 1e-12, `frame ${i}`);
      close(s.drift, e.drift, 1e-9, `frame ${i} drift`);
      if (e.drift) expect(Object.keys(s.drift!).sort()).toEqual(Object.keys(e.drift).sort());
      expect(s.notices, `frame ${i} notices`).toEqual(e.notices);
      close(s.jitter_deg, e.jitter_deg, 1e-9, `frame ${i} jitter`);
      expect(Object.keys(s.components).sort(), `frame ${i}`).toEqual(
        Object.keys(e.components).sort(),
      );
      expect(monitor.shouldEmit(s), `frame ${i}`).toBe(e.emit);
    });
  });

  it.each(rows(FX.gate).map((g, i): Row => ({ ...g, i, ref: g.reference ? 'reference' : 'none' })))(
    'calibration cue direction: $cue, $ref',
    (g) => {
      const cfg = { ...CFG.calibration, cue_timeout_ms: 5000, min_samples_per_class: 5 };
      const gauge = new CalibrationGauge(cfg, 0.7, false);
      gauge.setDirectionReference(g.reference);
      gauge.start(g.cue, 0);
      (g.frames as (ObsSpec & { conf: number })[]).forEach((f, i) => {
        const obs = makeObs(f);
        const res = gauge.offer(
          obs,
          { yaw: obs.headPose.yaw, pitch: obs.headPose.pitch, confidence: f.conf },
          f.t,
        );
        expect(res, `frame ${i}`).toEqual(g.results[i]);
      });
      const st = gauge.status;
      expect([st.state, st.good, st.dominant_reason]).toEqual([
        g.status.state,
        g.status.good,
        g.status.dominant,
      ]);
      expect(st.rejected).toEqual(g.status.rejected);
    },
  );

  it('gauge: a short target (screen-centre confirmation), then extended to a full look', () => {
    const cfg = {
      ...CFG.calibration,
      target_good_frames: 12,
      min_samples_per_class: 5,
      cue_timeout_ms: 4000,
    };
    const gauge = new CalibrationGauge(cfg, 0.7, false);
    gauge.start('SCREEN', 0, 6);
    let extended = false;
    for (const [i, e] of (
      FX.gauge_extend.steps as {
        t: number;
        pitch: number;
        ok: boolean;
        reason: string | null;
        state: string;
        good: number;
        target: number;
      }[]
    ).entries()) {
      const obs = makeObs({ t: e.t, pitch: (e.pitch * 180) / Math.PI });
      const res = gauge.offer(
        obs,
        { yaw: obs.headPose.yaw, pitch: obs.headPose.pitch, confidence: 0.9 },
        e.t,
      );
      if (gauge.status.state === 'DONE' && !extended) {
        gauge.extend(12);
        extended = true;
      }
      const st = gauge.status;
      expect([res[0], res[1], st.state, st.good, st.target], `step ${i}`).toEqual([
        e.ok,
        e.reason,
        e.state,
        e.good,
        e.target,
      ]);
    }
  });

  it.each(rows(FX.gauge))('gauge, eye_based=$eye_based', (g) => {
    const cfg = {
      ...CFG.calibration,
      target_good_frames: 16,
      min_samples_per_class: 5,
      cue_timeout_ms: 4000,
    };
    const gauge = new CalibrationGauge(cfg, 0.7, g.eye_based);
    gauge.start('CAMERA', 0);
    (g.frames as (ObsSpec & { conf: number })[]).forEach((f, i) => {
      const obs = makeObs(f);
      const res = gauge.offer(
        obs,
        { yaw: obs.headPose.yaw, pitch: obs.headPose.pitch, confidence: f.conf },
        f.t,
      );
      expect(res, `frame ${i}`).toEqual(g.results[i]);
    });
    const st = gauge.status;
    expect([st.state, st.good, st.dominant_reason]).toEqual([
      g.status.state,
      g.status.good,
      g.status.dominant,
    ]);
    expect(st.rejected).toEqual(g.status.rejected);
  });
});

/** Same keys at every level (the agents read these dicts), then `close` on the values. */
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

describe('1 s records', () => {
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

  it('frames -> 1 s slices (vote, UNCERTAIN, UNMEASURED gaps, OTHER direction)', () => {
    sameDict(slice(ev.frames, ev.t_end).map(sampleToDict), ev.samples);
  });

  it('a second take', () => {
    const take2 = slice(ev.take2.frames, ev.take2.frames.at(-1).t_ms + 125);
    sameDict(take2.map(sampleToDict), ev.take2.samples);
  });
});

describe('head circle check', () => {
  it.each(rows(FX.sweep))('$name', (c) => {
    const sweep = new HeadSweep({ ...CFG.sweep, ...c.overrides });
    const frames = c.frames as ObsSpec[];
    sweep.start(frames[0]!.t - 125);
    frames.forEach((f, i) => sameDict(sweep.offer(makeObs(f), f.t), c.statuses[i], `frame ${i}`));
  });
});
