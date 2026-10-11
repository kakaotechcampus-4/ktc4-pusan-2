/**
 * The engine end to end over a fake face detector: the frontend's batch flow,
 * the demo's gauge flow, decisions, conditions and re-anchoring.
 */
import { describe, expect, it } from 'vitest';
import { makeConfig } from '../src/engine/config';
import { GazeEngine, type EngineSettings } from '../src/engine/engine';
import { GazeEvidenceRecorder } from '../src/engine/evidence';
import { observe } from '../src/engine/observe';
import { FakeDetector, frame, type FakeFrame } from './helpers';

/** Lens, screen centre and script postures (head pitch, degrees; a laptop camera sees ~18 at the lens). */
const LENS = 18;
const SCREEN = 11;
const SCRIPT = 4;

const lumaStub = () => ({ measure: () => ({ face: 120, background: 100 }) });

function engine(settings: EngineSettings = {}) {
  const detector = new FakeDetector();
  const e = new GazeEngine<FakeFrame>({ detector, luma: lumaStub }, settings);
  return { e, detector };
}

/** 16 frames around a posture, with a little deterministic jitter. */
const look = (pitch: number, yaw = 0.5, n = 16): FakeFrame[] =>
  Array.from({ length: n }, (_, i) =>
    frame(yaw + ((i % 3) - 1) * 0.2, pitch + ((i % 5) - 2) * 0.15),
  );

function calibrated(settings: EngineSettings = {}) {
  const { e, detector } = engine(settings);
  const placement = e.checkPlacement(look(LENS), look(SCREEN));
  const out = e.fitCalibration(look(LENS), look(SCRIPT));
  return { e, detector, placement, out };
}

describe('frontend batch flow', () => {
  it('reads the camera as TOP and reuses the screen-centre look as the SCREEN cue', () => {
    const { placement, out } = calibrated();
    expect(placement.placement).toBe('TOP');
    expect(placement.supported).toBe(true);
    expect(out.quality.status).toBe('OK');
    expect([out.quality.n_camera, out.quality.n_screen, out.quality.n_bottom]).toEqual([
      16, 16, 16,
    ]);
    expect(out.model!.classes).toEqual(['CAMERA', 'SCREEN', 'BOTTOM', 'OTHER']);
  });

  it('can be told not to reuse the placement look', () => {
    const { out } = calibrated({ reuseScreenFromPlacement: false });
    expect(out.model!.classes).toEqual(['CAMERA', 'BOTTOM', 'OTHER']);
    expect(out.quality.n_screen).toBe(0);
  });

  it('maps the four classes onto the three frontend zones', () => {
    const { e } = calibrated();
    const at = (pitch: number, yaw = 0.5) => e.classify(frame(yaw, pitch), 100_000 + Math.random());
    expect([at(LENS).state, at(LENS).label]).toEqual(['CAMERA', 'CAMERA']);
    expect([at(SCREEN).state, at(SCREEN).label]).toEqual(['SCREEN', 'BOTTOM']);
    expect([at(SCRIPT).state, at(SCRIPT).label]).toEqual(['BOTTOM', 'BOTTOM']);
    const away = at(LENS, 50);
    expect([away.state, away.label, away.face_valid]).toEqual(['OTHER', 'UNCERTAIN', true]);
  });

  it('judges a lowered head whose eyelids read as closed (the head pose needs no eyes)', () => {
    const { e } = calibrated();
    const shown = frame(0.5, SCRIPT, { eyesClosed: true });
    // The preprocess gate itself still calls it EYES_CLOSED (eye backbones rely on it) ...
    const raw = observe(
      new FakeDetector().detect(shown as never, 0),
      [640, 480],
      0,
      e.cfg.preprocess,
    );
    expect(raw.invalidReason).toBe('EYES_CLOSED');
    // ... but the head-pose engine keeps the frame and judges the head direction.
    const obs = e.observe(shown, 100_000);
    expect(obs.quality.minEyeOpenness).toBeLessThan(e.cfg.preprocess.min_eye_openness);
    expect([obs.faceValid, obs.invalidReason]).toEqual([true, null]);
    const d = e.decide(obs);
    expect([d.state, d.label, d.face_valid, d.uncertain_reason]).toEqual([
      'BOTTOM',
      'BOTTOM',
      true,
      null,
    ]);
  });

  it('a head raised or turned a little is the nearest target; only a clear look away is OTHER', () => {
    const { e } = calibrated();
    let t = 300_000;
    const at = (pitch: number, yaw = 0.5) => e.classify(frame(yaw, pitch), (t += 125)).state;
    expect(at(LENS + 5)).toBe('CAMERA'); // chin up a little above the lens
    expect(at(SCREEN, -14)).toBe('SCREEN'); // toward the screen's side
    expect(at(LENS + 16)).toBe('OTHER'); // well above
    expect(at(SCREEN, -40)).toBe('OTHER'); // well to the side
  });

  it('can count OTHER as "not the audience" instead of "could not tell"', () => {
    const { e } = calibrated({ otherAs: 'BOTTOM' });
    const away = e.classify(frame(50, LENS), 200_000);
    expect([away.state, away.label]).toEqual(['OTHER', 'BOTTOM']);
  });

  it('gives a stored model that survives structuredClone and restores into a new engine', () => {
    const { out } = calibrated();
    const stored = structuredClone(out.model);
    expect(stored).toEqual(out.model);
    const { e: fresh } = engine();
    expect(fresh.calibrate(stored)).toBe(true);
    expect(fresh.classify(frame(0.5, SCRIPT), 1000).label).toBe('BOTTOM');
    expect(fresh.calibrate({ schema: 'something-else' })).toBe(false);
  });

  it('keeps batch timestamps strictly increasing for MediaPipe VIDEO mode', () => {
    const { e, detector } = calibrated();
    e.classify(frame(0.5, LENS), 10); // a caller clock far behind the batch clock
    const calls = detector.calls;
    expect(calls.every((t, i) => i === 0 || t > calls[i - 1]!)).toBe(true);
  });

  it('abstains before calibration and on frames without a face', () => {
    const { e } = engine();
    expect(e.classify(frame(0.5, LENS), 1)).toMatchObject({
      label: 'UNCERTAIN',
      face_valid: false,
      uncertain_reason: 'NOT_CALIBRATED',
    });
    const { e: ready } = calibrated();
    expect(ready.classify(frame(0, 0, { empty: true }), 300_000)).toMatchObject({
      face_valid: false,
      uncertain_reason: 'NO_FACE',
    });
  });

  it('a placement batch without one usable frame is NOT_ENOUGH_SAMPLES, not an exception', () => {
    const { e } = engine();
    const out = e.checkPlacement([frame(0, 0, { empty: true })], look(SCREEN));
    expect([out.placement, out.reason, out.n_camera]).toEqual([
      'INCONCLUSIVE',
      'NOT_ENOUGH_SAMPLES',
      0,
    ]);
  });
});

describe('live conditions', () => {
  it('reports reliability with each decision and forces UNCERTAIN once the face is gone', () => {
    const { e } = calibrated();
    const ok = e.classify(frame(0.5, LENS), 400_000);
    expect(ok.condition!.reliability).toBe(1);
    let last = ok;
    for (let t = 400_125; t < 402_000; t += 125) last = e.classify(frame(0, 0, { empty: true }), t);
    expect(last.condition!.severe).toBe(true);
    expect(last.condition!.issues[0]).toBe('FACE_LOST');
    expect(last).toMatchObject({
      label: 'UNCERTAIN',
      face_valid: false,
      uncertain_reason: 'FACE_LOST',
    });
  });

  it('does not count dipping toward the calibrated script as a drift', () => {
    const { e } = calibrated();
    const d = e.classify(frame(0.5, SCRIPT), 500_000);
    expect(d.condition!.issues).not.toContain('HEAD_TURNED');
  });

  it('a move far from the calibration position stops the judgement, and says how far', () => {
    const { e } = calibrated();
    let d = e.classify(frame(0.5, LENS), 950_000);
    expect(d.condition!.drift!.deg).toBeLessThan(0.5);
    for (let t = 950_125; t < 953_000; t += 125)
      d = e.classify(frame(0.5, LENS, { moved: [0.12, 0] }), t); // ~12 % of the frame toward image right
    const drift = d.condition!.drift!;
    expect(drift.right_cm).toBeLessThan(0); // image right is the presenter's left
    expect(drift.deg).toBeGreaterThan(drift.fail_deg);
    expect(d.condition!.issues[0]).toBe('MOVED_TOO_FAR');
    expect([d.state, d.uncertain_reason]).toEqual(['UNCERTAIN', 'MOVED_TOO_FAR']);
  });

  it('notes a second person once seen for a second, without lowering reliability', () => {
    const { e } = calibrated();
    let t = 600_000;
    const at = (twoFaces: boolean) => e.classify(frame(0.5, LENS, { twoFaces }), (t += 125));
    for (let i = 0; i < 4; i++) expect(at(true).condition!.notices).toEqual([]);
    at(false);
    let d = at(true);
    for (let i = 0; i < 8; i++) d = at(true);
    expect(d.condition!.notices).toEqual(['SECOND_FACE']);
    expect([d.condition!.issues, d.condition!.reliability]).toEqual([[], 1]);
    expect(e.cfg.condition.second_face_confirm_ms).toBe(1000);
  });
});

describe('direction and agent evidence', () => {
  it("says which way an OTHER look went, from the presenter's side", () => {
    const { e } = calibrated();
    let t = 700_000;
    const at = (pitch: number, yaw = 0.5) => e.classify(frame(yaw, pitch), (t += 125));
    // yaw > 0 turns toward the image right, which is the presenter's own left
    expect([at(LENS, 50).state, at(LENS, 50).direction]).toEqual(['OTHER', 'LEFT']);
    expect([at(LENS, -50).state, at(LENS, -50).direction]).toEqual(['OTHER', 'RIGHT']);
    expect([at(55).state, at(55).direction]).toEqual(['OTHER', 'UP']);
    expect([at(-35).state, at(-35).direction]).toEqual(['OTHER', 'DOWN']);
    expect(at(-35, -40).direction).toBe('DOWN_RIGHT');
    const lens = at(LENS);
    expect([lens.state, lens.direction, lens.offset_deg]).toEqual(['CAMERA', null, [0, 0]]);
    expect(at(LENS, -50).offset_deg![0]).toBeGreaterThan(0);
  });

  it('turns frame decisions into the 1 s records the coach and review agents read', () => {
    const { e } = calibrated();
    const rec = new GazeEvidenceRecorder(makeConfig().evidence);
    let t = 800_000;
    const feed = (pitch: number, yaw: number, seconds: number) => {
      for (let i = 0; i < seconds * 8; i++) rec.record(e.classify(frame(yaw, pitch), (t += 125)));
    };
    feed(LENS, 0.5, 4);
    feed(SCRIPT, 0.5, 5);
    const script = rec.issues();
    expect(script[0]).toMatchObject({
      evaluator: 'gaze',
      issue_type: 'GAZE_ON_SCRIPT',
      actionable: true,
    });
    expect(script[0]!.persistence_sec).toBeGreaterThanOrEqual(4);
    feed(LENS, -50, 3);
    const away = rec.issues().find((i) => i.issue_type === 'GAZE_AWAY')!;
    expect(away.evidence.direction).toBe('RIGHT');
    rec.flush(t + 125);
    const summary = rec.summary() as { problem_segments: { issue_type: string }[] };
    expect(summary.problem_segments.map((p) => p.issue_type)).toEqual([
      'GAZE_ON_SCRIPT',
      'GAZE_AWAY',
    ]);
  });
});

describe('head circle check', () => {
  it("fills the ring around the centre pose and reads the presenter's own directions", () => {
    const { e } = engine();
    const cfg = e.cfg.sweep;
    let t = 0;
    e.startSweep(t);
    for (let i = 0; i < cfg.neutral_frames; i++) e.offerSweepFrame(frame(0.5, SCREEN), (t += 125));
    // A quarter turn to the presenter's right (image-left, yaw < 0) lights RIGHT first.
    let status = e.offerSweepFrame(frame(0.5 - 8, SCREEN), (t += 125)).status;
    status = e.offerSweepFrame(frame(0.5 - 18, SCREEN), (t += 125)).status;
    expect(status.state).toBe('SWEEPING');
    expect(status.ticks[0]).toBe(true);
    for (let k = 0; k <= 26; k++) {
      const a = (2 * Math.PI * k) / 26;
      const right = 1.3 * cfg.reach_yaw_deg * Math.cos(a);
      const up = 1.3 * cfg.reach_pitch_deg * Math.sin(a);
      status = e.offerSweepFrame(frame(0.5 - right, SCREEN + up), (t += 125)).status;
    }
    expect(status.state).toBe('DONE');
    expect(status.missing).toEqual([]);
    expect(e.calibrationCounts).toEqual({ CAMERA: 0, SCREEN: 0, BOTTOM: 0 });
  });

  it('pauses without a face and keeps what was filled', () => {
    const { e } = engine();
    let t = 0;
    e.startSweep(t);
    for (let i = 0; i < e.cfg.sweep.neutral_frames; i++)
      e.offerSweepFrame(frame(0.5, SCREEN), (t += 125));
    e.offerSweepFrame(frame(0.5 - 10, SCREEN), (t += 125));
    const lit = e.offerSweepFrame(frame(0.5 - 20, SCREEN), (t += 125)).status.filled;
    const lost = e.offerSweepFrame(frame(0, 0, { empty: true }), (t += 125)).status;
    expect(lost.last_reason).toBe('NO_FACE');
    expect(lost.filled).toBe(lit);
    expect(lost.lost).toEqual({ RIGHT: 1 });
  });
});

describe('cues read against the head circle', () => {
  it('a cue fills only while the head points its way from the circle centre', () => {
    const { e } = engine();
    let t = 0;
    e.startSweep(t);
    for (let i = 0; i < e.cfg.sweep.neutral_frames; i++)
      e.offerSweepFrame(frame(0.5, SCREEN), (t += 125));
    e.startCue('CAMERA', (t += 125));
    const start = t;
    const offer = (yaw: number, pitch: number) =>
      e.offerCalibrationFrame(frame(yaw, pitch), (t += 125)).status;
    while (t - start < e.cfg.calibration.cue_settle_ms) offer(0.5, SCREEN);
    let status = offer(0.5, SCREEN); // still looking at the screen
    expect([status.good, status.last_reason]).toEqual([0, 'LOOK_HIGHER']);
    status = offer(-14, LENS); // up, but turned far to the side
    expect([status.good, status.last_reason]).toEqual([0, 'OFF_TARGET']);
    for (let i = 0; i < 16; i++) status = offer(0.5 + ((i % 3) - 1) * 0.2, LENS);
    expect(status.state).toBe('DONE');
  });

  /** The head circle's first second at `pitch`, then a cue held at `cuePitch` until it ends. */
  function circleThenScreen(pitch: number, cuePitch: number) {
    const { e } = engine();
    let t = 0;
    e.startSweep(t);
    for (let i = 0; i < e.cfg.sweep.neutral_frames; i++)
      e.offerSweepFrame(frame(0.5, pitch), (t += 125));
    e.startCue('SCREEN', (t += 125));
    const first = e.offerCalibrationFrame(frame(0.5, cuePitch), (t += 125)).status;
    let status = first;
    for (let i = 0; i < 60 && !status.finished; i++)
      status = e.offerCalibrationFrame(frame(0.5, cuePitch), (t += 125)).status;
    return { e, first, status, t };
  }

  it('the screen-centre look confirms the circle centre and folds its frames in', () => {
    const { e, first, status } = circleThenScreen(SCREEN, SCREEN);
    const cal = e.cfg.calibration;
    expect(first.target).toBe(cal.cue_confirm_frames);
    expect([status.state, status.good]).toEqual(['DONE', cal.cue_confirm_frames]);
    expect(e.baselineCheck).toEqual({
      confirmed: true,
      shift_deg: expect.closeTo(0, 6),
      seeded: e.cfg.sweep.neutral_frames,
    });
    expect(e.calibrationCounts.SCREEN).toBe(cal.cue_confirm_frames + e.cfg.sweep.neutral_frames);
  });

  it('a presenter who moved since the circle is measured again, and the cues follow', () => {
    const { e, status, t } = circleThenScreen(SCREEN, SCREEN + 5);
    const cal = e.cfg.calibration;
    expect([status.state, status.good]).toEqual(['DONE', cal.target_good_frames]);
    expect(e.baselineCheck!.confirmed).toBe(false);
    expect(e.baselineCheck!.shift_deg).toBeCloseTo(5, 6);
    expect(e.calibrationCounts.SCREEN).toBe(cal.target_good_frames);
    // The script is now read from the new posture: 5 deg below it fills (only 0 below the circle).
    e.startCue('BOTTOM', t + 125);
    let s = e.offerCalibrationFrame(frame(0.5, SCREEN), t + 250).status;
    for (let i = 0; i < 40 && !s.finished; i++)
      s = e.offerCalibrationFrame(frame(0.5, SCREEN), t + 375 + i * 125).status;
    expect(s.state).toBe('DONE');
  });

  it('turning the head far away is a direction, not a worse measurement', () => {
    const { e } = calibrated();
    let d = e.classify(frame(0.5, LENS), 900_000);
    for (let t = 900_125; t < 901_000; t += 125) d = e.classify(frame(-45, LENS + 20), t);
    expect(d.state).toBe('OTHER');
    expect(d.direction).toBe('UP_RIGHT');
    expect(d.condition!.reliability).toBe(1);
    expect(d.condition!.issues).not.toContain('HEAD_TURNED');
  });
});

describe('demo gauge flow', () => {
  it('fills each cue on good frames, lets the head move between cues, and fits', () => {
    const { e } = engine();
    let t = 0;
    for (const [cue, pitch] of [
      ['CAMERA', LENS],
      ['SCREEN', SCREEN],
      ['BOTTOM', SCRIPT],
    ] as const) {
      e.startCue(cue, t);
      let status = null;
      for (const f of look(pitch, 0.5, 40)) {
        status = e.offerCalibrationFrame(f, t).status;
        t += 125;
        if (status.finished) break;
      }
      expect(status!.state).toBe('DONE');
      expect(status!.rejected.HEAD_MOVED ?? 0).toBe(0);
    }
    expect(e.estimatePlacement()!.placement).toBe('TOP');
    const out = e.finishCalibration();
    expect(out.quality.status).toBe('OK');
    expect((out.quality.placement as { placement: string }).placement).toBe('TOP');
    expect(e.calibrationCounts).toEqual({ CAMERA: 16, SCREEN: 16, BOTTOM: 16 });
  });
});

describe('re-anchor', () => {
  it('moves every anchor by the lens offset and accepts the new posture', () => {
    const { e } = calibrated();
    e.beginReanchor(700_000);
    let t = 700_000;
    for (let i = 0; i < 8; i++) e.classify(frame(0.5, LENS - 3), (t += 125));
    const st = e.reanchorStatus;
    expect(st.state).toBe('DONE');
    expect(st.shift_deg![1]).toBeCloseTo(-3, 0);
    expect(e.classify(frame(0.5, LENS - 3), (t += 125)).state).toBe('CAMERA');
  });

  it('refuses a shift too large to be a look at the lens', () => {
    const { e } = calibrated();
    e.beginReanchor(800_000);
    let t = 800_000;
    for (let i = 0; i < 8; i++) e.classify(frame(0.5, SCRIPT), (t += 125));
    expect(e.reanchorStatus).toMatchObject({ state: 'REJECTED', reason: 'SHIFT_TOO_LARGE' });
  });
});

describe('set-up check through the engine', () => {
  it('passes a centred, lit, frontal face after the hold time', () => {
    const { e } = engine();
    let report = null;
    for (let t = 0; t <= 1500; t += 125) report = e.checkPreconditions(frame(0, 10), t).report;
    expect(report!.status).toBe('PASS');
  });

  it('rejects a second person three seconds after it is confirmed', () => {
    const { e } = engine();
    let report = null;
    for (let t = 0; t <= 4000; t += 125)
      report = e.checkPreconditions(frame(0, 10, { twoFaces: true }), t).report;
    expect([report!.status, report!.reason]).toEqual(['REJECT', 'MULTIPLE_FACES']);
  });
});

it('dispose releases the detector and refuses further frames', () => {
  const { e, detector } = engine();
  e.dispose();
  expect(detector.closed).toBe(true);
  expect(() => e.observe(frame(0, 0), 1)).toThrow();
});
