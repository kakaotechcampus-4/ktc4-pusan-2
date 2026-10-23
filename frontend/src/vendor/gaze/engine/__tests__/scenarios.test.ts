/**
 * The judgement criteria, one scenario at a time, through the browser engine
 * end to end (fake face detector -> observe -> head pose -> classifier ->
 * conditions).  Same scenario table as the Python `tests/test_scenarios.py`
 * (v1 README §7-13).  Head angles are raw-frame degrees (yaw > 0 turns toward
 * the image right = the presenter's left); directions are presenter-centric.
 */
import { describe, expect, it } from 'vitest';
import { GazeEngine } from '../engine';
import { FakeDetector, frame, lumaOf, type FakeFrame } from './helpers';

const lumaStub = lumaOf;

/** Lens, screen centre and script postures (head yaw, pitch). */
const LENS: [number, number] = [0.5, 18];
const SCREEN: [number, number] = [0.5, 11];
const SCRIPT: [number, number] = [0.5, 4];

const look = ([yaw, pitch]: [number, number], n = 16): FakeFrame[] =>
  Array.from({ length: n }, (_, i) =>
    frame(yaw + ((i % 3) - 1) * 0.2, pitch + ((i % 5) - 2) * 0.15),
  );

function take(lens = LENS, screen = SCREEN, script = SCRIPT) {
  const e = new GazeEngine<FakeFrame>({ detector: new FakeDetector(), luma: lumaStub });
  e.checkPlacement(look(lens), look(screen));
  const out = e.fitCalibration(look(lens), look(script));
  expect(out.quality.status).toBe('OK');
  let t = 100_000;
  /** Hold a posture for a few frames; the last decision. */
  const hold = (pose: [number, number], extra: Partial<FakeFrame> = {}, frames = 4) => {
    let d = e.classify(frame(pose[0], pose[1], extra), (t += 125));
    for (let i = 1; i < frames; i++) d = e.classify(frame(pose[0], pose[1], extra), (t += 125));
    return d;
  };
  return { e, hold };
}

const at = (pose: [number, number], dYaw: number, dPitch = 0): [number, number] => [
  pose[0] + dYaw,
  pose[1] + dPitch,
];

describe('where the presenter looks', () => {
  it('S01 the lens is eye contact', () => {
    const d = take().hold(LENS);
    expect([d.state, d.label]).toEqual(['CAMERA', 'CAMERA']);
  });

  it('S02 the screen centre is the screen, at 0 deg left/right and up/down', () => {
    const d = take().hold(SCREEN);
    expect(d.state).toBe('SCREEN');
    expect(d.aim_deg![0]).toBeCloseTo(0, 6);
    expect(d.aim_deg![1]).toBeCloseTo(0, 6);
  });

  it('S03 reading the script with lowered lids is the script', () => {
    const d = take().hold(SCRIPT, { eyesClosed: true });
    expect([d.state, d.face_valid]).toEqual(['BOTTOM', true]);
  });

  it("S04 the screen's side is still the screen, and says how far", () => {
    const { hold } = take();
    const right = hold(at(SCREEN, -8));
    expect(right.state).toBe('SCREEN');
    expect(right.aim_deg![0]).toBeCloseTo(8, 6);
    const left = hold(at(SCREEN, 8));
    expect(left.state).toBe('SCREEN');
    expect(left.aim_deg![0]).toBeCloseTo(-8, 6);
  });

  it.each([
    [-25, 'RIGHT'],
    [25, 'LEFT'],
  ] as const)('S05 a clear look %i deg aside is elsewhere, %s', (turn, side) => {
    const d = take().hold(at(SCREEN, turn));
    expect([d.state, d.direction, d.label]).toEqual(['OTHER', side, 'UNCERTAIN']);
    expect(d.condition!.reliability).toBe(1);
  });

  it('S06 well above the lens is elsewhere, up', () => {
    const d = take().hold(at(LENS, 0, 12));
    expect([d.state, d.direction]).toEqual(['OTHER', 'UP']);
  });

  it('S07 a chin a little above the lens is still the lens', () => {
    expect(take().hold(at(LENS, 0, 3)).state).toBe('CAMERA');
  });

  it('S08 far below the script is elsewhere, down', () => {
    const d = take().hold(at(SCRIPT, 0, -15));
    expect([d.state, d.direction]).toEqual(['OTHER', 'DOWN']);
  });

  it('S09 a head turned far with the eyes out of sight is still judged', () => {
    const d = take().hold(at(SCREEN, 40), { eyesClosed: true });
    expect([d.face_valid, d.state, d.direction]).toEqual([true, 'OTHER', 'LEFT']);
  });

  it('S10 between the screen and the script is never elsewhere', () => {
    const d = take().hold([SCRIPT[0], (SCREEN[1] + SCRIPT[1]) / 2]);
    expect(d.state).not.toBe('OTHER');
    expect(d.label).toBe('BOTTOM'); // the frontend's "screen/script" zone
  });

  it('S11 a head lifted far for the lens still gets left and right', () => {
    const { hold } = take([0.5, 26], [0.5, 11], [0.5, 3]);
    const d = hold([0.5 - 22, 11]);
    expect([d.state, d.direction]).toEqual(['OTHER', 'RIGHT']);
    expect(hold([0.5 - 8, 11]).state).toBe('SCREEN');
  });

  it('S20 a blink at the lens does not interrupt the judgement', () => {
    const { hold } = take();
    hold(LENS);
    const d = hold(LENS, { eyesClosed: true }, 2);
    expect([d.face_valid, d.state]).toEqual([true, 'CAMERA']);
  });
});

describe('who is in the picture', () => {
  it('S12 leaning back alone is never another person', () => {
    const { hold } = take();
    let d = hold(SCREEN);
    // The fake face is ~2.2 % of the frame: down to 75 % of its size stays above the detector floor.
    for (let i = 0; i < 24; i++) d = hold(SCREEN, { scale: 1 - (0.25 * i) / 23 }, 1);
    expect(d.face_valid).toBe(true);
    expect(d.condition!.notices).toEqual([]);
    expect(d.condition!.issues).not.toContain('FACE_REPLACED');
    expect(d.condition!.severe).toBe(false);
  });

  it('S13 the same face found twice is not another person', () => {
    const d = take().hold(SCREEN, { duplicate: true }, 16);
    expect(d.condition!.notices).toEqual([]);
  });

  it('S14 a tiny find in the background is not another person', () => {
    const d = take().hold(SCREEN, { scale: 0.8, tinyFind: true }, 16);
    expect(d.face_valid).toBe(true);
    expect(d.condition!.notices).toEqual([]);
  });

  it('S15 someone passing for a moment is ignored', () => {
    const { hold } = take();
    hold(SCREEN, { twoFaces: true }, 3);
    const d = hold(SCREEN, {}, 8);
    expect(d.condition!.notices).toEqual([]);
  });

  it('S16 a second person staying is a notice, the judgement goes on', () => {
    const { hold } = take();
    const early = hold(LENS, { twoFaces: true }, 4);
    expect(early.condition!.notices).toEqual([]);
    const d = hold(LENS, { twoFaces: true }, 6);
    expect(d.condition!.notices).toEqual(['SECOND_FACE']);
    expect([d.condition!.issues, d.condition!.reliability]).toEqual([[], 1]);
    expect([early.state, d.state]).toEqual(['CAMERA', 'CAMERA']);
  });

  it('S17 another person in the seat stops the judgement', () => {
    const { hold } = take();
    hold(SCREEN);
    const d = hold(SCREEN, { moved: [0.32, -0.12], scale: 1.3 }, 5);
    expect(d.condition!.severe).toBe(true);
    expect(d.condition!.issues[0]).toBe('FACE_REPLACED');
    expect([d.state, d.uncertain_reason]).toEqual(['UNCERTAIN', 'FACE_REPLACED']);
  });
});

describe('who is in the picture, right after calibration', () => {
  it('S17b someone else sitting down right after calibration is caught', () => {
    const d = take().hold(SCREEN, { moved: [0.32, -0.12], scale: 1.3 }, 5);
    expect(d.condition!.issues[0]).toBe('FACE_REPLACED');
  });
});

describe('measurement problems', () => {
  it('S18 moving seat far for two seconds makes the measurement unusable', () => {
    const { hold } = take();
    hold(SCREEN);
    const d = hold(SCREEN, { moved: [0.18, 0] }, 20);
    expect(d.condition!.issues[0]).toBe('MOVED_TOO_FAR');
    expect([d.state, d.uncertain_reason]).toEqual(['UNCERTAIN', 'MOVED_TOO_FAR']);
  });

  it('S19 a face gone for a second and a half is unmeasurable', () => {
    const { e, hold } = take();
    hold(SCREEN);
    const short = hold(SCREEN, { empty: true }, 4);
    expect([short.condition!.severe, short.uncertain_reason]).toEqual([false, 'NO_FACE']);
    const lost = hold(SCREEN, { empty: true }, e.cfg.condition.face_lost_ms / 125);
    expect(lost.condition!.issues[0]).toBe('FACE_LOST');
    expect(hold(SCREEN).state).toBe('SCREEN');
  });
});

describe('what counts against reliability', () => {
  it('S21 a light change alone does not lower reliability', () => {
    const { hold } = take();
    hold(LENS);
    const d = hold(LENS, { brightness: 45 }, 8);
    expect([d.condition!.reliability, d.condition!.issues, d.state]).toEqual([1, [], 'CAMERA']);
  });

  it('S22 shaky head angles lower reliability', () => {
    const { e } = take();
    let t = 300_000;
    let d = e.classify(frame(LENS[0], LENS[1]), t);
    for (let i = 0; i < 16; i++) d = e.classify(frame(LENS[0], LENS[1]), (t += 125));
    expect(d.condition!.jitter_deg!).toBeLessThan(0.5);
    for (let i = 0; i < 16; i++) {
      const shake = i % 2 ? 3 : -3;
      d = e.classify(frame(LENS[0] + shake, LENS[1] + shake), (t += 125));
    }
    expect(d.condition!.issues).toContain('NOISY_TRACKING');
    expect(d.condition!.reliability).toBeLessThan(1);
    expect(d.condition!.severe).toBe(false);
  });
});

describe('the head circle measures the baseline; the calibration confirms and completes it', () => {
  /** The demo's gauge flow: the circle's first second, then screen centre -> lens -> script. */
  function gaugeTake(
    looks: [[number, number], [number, number], [number, number]],
    circleAt: [number, number],
  ) {
    const e = new GazeEngine<FakeFrame>({ detector: new FakeDetector(), luma: lumaStub });
    let t = 0;
    e.startSweep(t);
    for (let i = 0; i < e.cfg.sweep.neutral_frames; i++)
      e.offerSweepFrame(frame(circleAt[0], circleAt[1]), (t += 125));
    const [lens, screen, script] = looks;
    const goods: Record<string, number> = {};
    for (const [cue, pose] of [
      ['SCREEN', screen],
      ['CAMERA', lens],
      ['BOTTOM', script],
    ] as const) {
      e.startCue(cue, (t += 125));
      let status = null;
      for (const f of look(pose, 80)) {
        status = e.offerCalibrationFrame(f, (t += 125)).status;
        if (status.finished) break;
      }
      expect(status!.state, cue).toBe('DONE');
      goods[cue] = status!.good;
    }
    const out = e.finishCalibration();
    expect(out.quality.status).toBe('OK');
    let clock = t;
    const hold = (pose: [number, number], frames = 4) => {
      let d = e.classify(frame(pose[0], pose[1]), (clock += 125));
      for (let i = 1; i < frames; i++) d = e.classify(frame(pose[0], pose[1]), (clock += 125));
      return d;
    };
    return { e, goods, hold };
  }

  it('S23 the screen-centre look confirms the head circle and is short', () => {
    const { e, goods, hold } = gaugeTake([LENS, SCREEN, SCRIPT], SCREEN);
    expect(e.baselineCheck!.confirmed).toBe(true);
    expect(e.baselineCheck!.seeded).toBe(e.cfg.sweep.neutral_frames);
    expect(goods.SCREEN).toBe(e.cfg.calibration.cue_confirm_frames);
    expect([hold(LENS).state, hold(SCRIPT).state]).toEqual(['CAMERA', 'BOTTOM']);
  });

  it('S24 a presenter who moved after the circle is measured again', () => {
    const up = (p: [number, number]): [number, number] => [p[0], p[1] + 5];
    const { e, goods, hold } = gaugeTake([up(LENS), up(SCREEN), up(SCRIPT)], SCREEN);
    expect(e.baselineCheck!.confirmed).toBe(false);
    expect(e.baselineCheck!.shift_deg).toBeCloseTo(5, 0);
    expect(goods.SCREEN).toBe(e.cfg.calibration.target_good_frames);
    expect([hold(up(LENS)).state, hold(up(SCRIPT)).state]).toEqual(['CAMERA', 'BOTTOM']);
  });
});

describe('the set-up check, same people', () => {
  it('P1 far but findable passes, and a duplicate find is one person', () => {
    const e = new GazeEngine<FakeFrame>({ detector: new FakeDetector(), luma: lumaStub });
    let report = null;
    for (let t = 0; t <= 1500; t += 125)
      report = e.checkPreconditions(frame(0, 10, { scale: 0.75, duplicate: true }), t).report;
    expect(report!.status).toBe('PASS');
  });

  it('P2 a second person fails the set-up once seen for half a second', () => {
    const e = new GazeEngine<FakeFrame>({ detector: new FakeDetector(), luma: lumaStub });
    let report = null;
    for (let t = 0; t <= 750; t += 125)
      report = e.checkPreconditions(frame(0, 10, { twoFaces: true }), t).report;
    expect(report!.reason).toBe('MULTIPLE_FACES');
  });
});
