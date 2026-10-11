/**
 * The head-pose gaze engine: one object per camera session (the browser
 * counterpart of `VisionSession` with the `head_pose` backbone).
 *
 * Two ways in:
 *
 * * **Frontend contract (A안)** -- `fitCalibration(camera, bottom)`,
 *   `checkPlacement(camera, screen)`, `calibrate(model)`, `classify(frame, tMs)`,
 *   `dispose()`, `version`.  Batches of frames in, Python-shaped dicts out, so
 *   `frontend/src/workers/aiAdapter.ts` maps them unchanged.  When
 *   `checkPlacement` ran first, its screen-centre frames become the SCREEN cue
 *   of the next `fitCalibration` -- the frontend already asks for that look.
 * * **Gauge flow (this repo's demo)** -- `startCue` / `offerCalibrationFrame` /
 *   `finishCalibration`, plus `checkPreconditions`, the head circle check
 *   (`startSweep` / `offerSweepFrame`) and `beginReanchor`.
 *
 * Nothing is persisted.  The calibration model is plain data the caller may
 * store and hand back through `calibrate()`.
 */
import {
  type ConditionState,
  ConditionMonitor,
  SceneBaselineAccumulator,
  shiftedHeadPoses,
  type SceneBaseline,
} from './condition';
import {
  CONFIG_HASH,
  makeConfig,
  VERSION,
  type ConfigOverrides,
  type EngineConfig,
  type VersionParts,
} from './config';
import { contractDecision, type FrameDecision, type OtherMapping } from './contract';
import { CalibrationGauge, type GaugeStatus, type GazeEstimate } from './gauge';
import type { FaceDetector } from './landmarker';
import { MAD_TO_SIGMA, median } from './math';
import { faceCentre, observe, type LumaSampler } from './observe';
import { placementFromAnchors, placementFromSamples, type PlacementResultDict } from './placement';
import { PreconditionChecker, type PreconditionReport } from './preconditions';
import { HeadSweep, type SweepStatus } from './sweep';
import {
  decideFrame,
  directionOf,
  fitReference,
  gazeOffset,
  aimOffset,
  isReferenceModel,
  shiftedModel,
  type CalibrationQualityDict,
  type ReferenceModelData,
} from './reference';
import {
  toDeg,
  type Cue,
  type InvalidReason,
  type Observation,
  type Point,
  type Sample,
  type StateClass,
} from './types';

/** Preprocess reasons that concern only the eyes, which the head-pose backbone never reads. */
const EYE_ONLY_REASONS: ReadonlySet<InvalidReason> = new Set(['EYES_CLOSED', 'CROP_FAILED']);

/** How the calibration's screen-centre look compared with the head circle's centre. */
export interface BaselineCheck {
  /** The look landed within `cue_confirm_deg` of the circle's centre. */
  confirmed: boolean;
  /** Distance between the two postures, degrees. */
  shift_deg: number;
  /** Circle-centre frames folded into the screen-centre samples (0 when remeasured). */
  seeded: number;
}

/** The calibration as the caller stores it: the model plus the scene it was taken in. */
export interface StoredModel extends ReferenceModelData {
  scene: SceneBaseline | null;
  configHash: string;
}

export interface CalibrationOutput {
  quality: CalibrationQualityDict;
  /** `null` when no model could be built (the frontend then asks for a retry). */
  model: StoredModel | null;
}

export type ReanchorState =
  'IDLE' | 'COLLECTING' | 'DONE' | 'REJECTED' | 'TIMED_OUT' | 'UNSUPPORTED';

export interface ReanchorStatus {
  state: ReanchorState;
  collected: number;
  target: number;
  shift_deg: [number, number] | null;
  reason: string | null;
}

export interface EngineDeps<F> {
  detector: FaceDetector;
  /** Brightness sampler for a frame; omit to leave brightness unmeasured. */
  luma?: (frame: F) => LumaSampler | null;
  /** Width and height of a frame. */
  size?: (frame: F) => readonly [number, number];
}

export interface EngineSettings {
  config?: ConfigOverrides;
  /** Where OTHER lands in the frontend's zones (default UNCERTAIN). */
  otherAs?: OtherMapping;
  /** Reuse the placement check's screen-centre frames as the SCREEN cue (default true). */
  reuseScreenFromPlacement?: boolean;
}

/** Frame spacing assumed for batches (the frontend grabs every 125 ms). */
const BATCH_STEP_MS = 125;

interface Measured {
  obs: Observation;
  gaze: GazeEstimate | null;
}

export class GazeEngine<F extends { width: number; height: number } = ImageBitmap> {
  readonly version: VersionParts = VERSION;
  readonly cfg: EngineConfig;
  readonly otherAs: OtherMapping;
  readonly #deps: EngineDeps<F>;
  readonly #reuseScreen: boolean;

  #clock = 0;
  #hint: Point | null = null;
  #model: ReferenceModelData | null = null;
  #monitor: ConditionMonitor | null = null;
  #quality: CalibrationQualityDict | null = null;
  #prechecker: PreconditionChecker;
  #sweep: HeadSweep;
  #placementCamera: Measured[] = [];
  #placementScreen: Measured[] = [];
  #placement: PlacementResultDict | null = null;
  // gauge flow
  #gauge: CalibrationGauge;
  /** The frames that made the head circle's centre, to fold into the screen-centre look. */
  #neutralFrames: { obs: Observation; gaze: GazeEstimate | null }[] = [];
  /** The screen-centre look is confirming the circle's centre (short target). */
  #confirming = false;
  #baselineCheck: BaselineCheck | null = null;
  #cue: Cue | null = null;
  #samples: Sample[] = [];
  #scene = new SceneBaselineAccumulator();
  // re-anchor
  #reanchor: ReanchorStatus;
  #reanchorStarted = 0;
  #reanchorObs: Observation[] = [];
  #reanchorGaze: [number, number][] = [];
  #disposed = false;
  /** Milliseconds spent on the last observed frame: MediaPipe detection, and everything (detection + engine). */
  lastTiming = { detectMs: 0, totalMs: 0 };

  constructor(deps: EngineDeps<F>, settings: EngineSettings = {}) {
    this.#deps = deps;
    this.cfg = makeConfig(settings.config);
    this.otherAs = settings.otherAs ?? 'UNCERTAIN';
    this.#reuseScreen = settings.reuseScreenFromPlacement ?? true;
    this.#prechecker = new PreconditionChecker(this.cfg.preconditions);
    this.#sweep = new HeadSweep(this.cfg.sweep);
    this.#gauge = new CalibrationGauge(this.cfg.calibration, 0.7, false);
    this.#reanchor = this.#idleReanchor();
  }

  // ------------------------------------------------------------------ basics

  get isCalibrated(): boolean {
    return this.#model !== null;
  }

  get quality(): CalibrationQualityDict | null {
    return this.#quality;
  }

  get placement(): PlacementResultDict | null {
    return this.#placement;
  }

  get classes(): readonly StateClass[] {
    return this.#model?.classes ?? [];
  }

  /** Analyse one frame: landmarks, head pose, validity, scene. */
  observe(frame: F, tMs: number): Observation {
    this.#assertOpen();
    const t = Math.max(Math.round(tMs), this.#clock + 1);
    this.#clock = t;
    const started = performance.now();
    const result = this.#deps.detector.detect(frame as unknown as TexImageSource, t);
    const detected = performance.now();
    const size = this.#deps.size ? this.#deps.size(frame) : ([frame.width, frame.height] as const);
    // Lazy: a frame without a face never pays for the pixel read-back.
    const lumaOf = this.#deps.luma;
    let sampler: LumaSampler | null | undefined;
    const luma: LumaSampler | null = lumaOf
      ? {
          measure: (b, s) =>
            (sampler === undefined ? (sampler = lumaOf(frame)) : sampler)?.measure(b, s) ?? null,
        }
      : null;
    const seen = observe(result, size, t, this.cfg.preprocess, { luma, hint: this.#hint });
    // The head-pose backbone never reads the eyes: lids lowered to read the
    // script, a blink, or eyes too small or turned away to crop leave the head
    // direction it measures intact.
    const obs: Observation =
      seen.invalidReason !== null && EYE_ONLY_REASONS.has(seen.invalidReason)
        ? { ...seen, faceValid: true, invalidReason: null }
        : seen;
    const centre = faceCentre(obs);
    if (centre) this.#hint = centre;
    this.lastTiming = { detectMs: detected - started, totalMs: performance.now() - started };
    return obs;
  }

  /** The head-pose backbone: gaze = head direction, confidence = landmarks in frame. */
  static gazeOf(obs: Observation): GazeEstimate | null {
    if (!obs.faceValid) return null;
    const { yaw, pitch, reprojectionError } = obs.headPose;
    if (!Number.isFinite(yaw) || !Number.isFinite(pitch) || !Number.isFinite(reprojectionError))
      return null;
    return { yaw, pitch, confidence: Math.min(1, Math.max(0, obs.presence)) };
  }

  // ------------------------------------------------------------ preconditions

  checkPreconditions(
    frame: F,
    tMs: number,
  ): { report: PreconditionReport; observation: Observation } {
    const observation = this.observe(frame, tMs);
    return { report: this.#prechecker.update(observation), observation };
  }

  resetPreconditions(): void {
    this.#prechecker.reset();
  }

  // ------------------------------------------------ head circle check (demo)

  /** Begin the ring: the next frames set its centre, then the head goes round. */
  startSweep(tMs: number): SweepStatus {
    this.#assertOpen();
    this.#neutralFrames = [];
    return this.#sweep.start(tMs);
  }

  /** One frame for the ring.  Keeps the frames of its centre for the screen-centre look. */
  offerSweepFrame(frame: F, tMs: number): { status: SweepStatus; observation: Observation } {
    const observation = this.observe(frame, tMs);
    const centering = this.#sweep.status.state === 'CENTERING';
    const status = this.#sweep.offer(observation, observation.tMs);
    if (centering && status.last_reason === null)
      this.#neutralFrames.push({ obs: observation, gaze: GazeEngine.gazeOf(observation) });
    return { status, observation };
  }

  get sweepStatus(): SweepStatus {
    return this.#sweep.status;
  }

  // ------------------------------------------------ frontend contract (A안)

  /** `GazeClassifier.checkPlacement`: lens frames, then screen-centre frames. */
  checkPlacement(camera: readonly F[], screen: readonly F[]): PlacementResultDict {
    this.#placementCamera = this.#batch(camera);
    this.#placementScreen = this.#batch(screen);
    const cam = this.#usable(this.#placementCamera);
    const scr = this.#usable(this.#placementScreen);
    const result =
      placementFromSamples(
        cam.map(degOf),
        scr.map(degOf),
        this.cfg.calibration,
        this.cfg.placement,
      ) ?? this.#emptyPlacement(cam.length, scr.length);
    this.#placement = result;
    return result;
  }

  /** `GazeClassifier.fitCalibration`: lens frames, then script-area frames. */
  fitCalibration(camera: readonly F[], bottom: readonly F[]): CalibrationOutput {
    const groups: [Cue, Measured[]][] = [
      ['CAMERA', this.#batch(camera)],
      ['BOTTOM', this.#batch(bottom)],
    ];
    if (this.#reuseScreen && this.#placementScreen.length)
      groups.push(['SCREEN', this.#placementScreen]);
    const scene = new SceneBaselineAccumulator();
    const samples: Sample[] = [];
    for (const [cue, measured] of groups) {
      for (const m of this.#dropGlances(this.#usable(measured))) {
        samples.push(sampleOf(cue, m));
        scene.add(m.obs, cue);
      }
    }
    return this.#fit(samples, scene.build());
  }

  /** `GazeClassifier.calibrate`: adopt a model from `fitCalibration` or storage. */
  calibrate(model: unknown): boolean {
    if (!isReferenceModel(model)) return false;
    const stored = model as Partial<StoredModel> & ReferenceModelData;
    this.#model = stripStored(stored);
    // The head direction IS the gaze here: turning away is a direction, not a worse measurement.
    this.#monitor = stored.scene
      ? new ConditionMonitor(this.cfg.condition, stored.scene, true)
      : null;
    this.#reanchor = this.#idleReanchor();
    return true;
  }

  /** `GazeClassifier.classify`: one unsmoothed frame decision. */
  classify(frame: F, tMs: number): FrameDecision {
    const obs = this.observe(frame, tMs);
    return this.decide(obs);
  }

  /** The decision for an already-observed frame (also feeds the monitor and re-anchor). */
  decide(obs: Observation): FrameDecision {
    const gaze = GazeEngine.gazeOf(obs);
    const headYaw = toDeg(obs.headPose.yaw);
    const headPitch = toDeg(obs.headPose.pitch);
    const base = {
      t_ms: obs.tMs,
      head_yaw_deg: headYaw,
      head_pitch_deg: headPitch,
      direction: null,
      offset_deg: null,
      aim_deg: null,
    };
    const model = this.#model;
    if (!model) {
      return {
        ...base,
        label: 'UNCERTAIN',
        state: 'UNCERTAIN',
        p_camera: 0.5,
        p_bottom: 0.5,
        face_valid: false,
        probs: null,
        uncertain_reason: 'NOT_CALIBRATED',
        condition: null,
      };
    }
    const condition: ConditionState | null = this.#monitor ? this.#monitor.update(obs) : null;

    let decision: FrameDecision;
    if (!gaze) {
      const reason = obs.invalidReason ?? (obs.faceValid ? 'BACKBONE_FAILED' : 'NO_FACE');
      decision = {
        ...base,
        label: 'UNCERTAIN',
        state: 'UNCERTAIN',
        p_camera: 0.5,
        p_bottom: 0.5,
        face_valid: false,
        probs: null,
        uncertain_reason: reason,
        condition,
      };
    } else {
      const cal = this.cfg.calibration;
      const { state, probs, reason } = decideFrame(
        model,
        toDeg(gaze.yaw),
        toDeg(gaze.pitch),
        headYaw,
        headPitch,
        cal,
      );
      const zone = contractDecision(probs, cal, this.otherAs);
      const offset = gazeOffset(
        model,
        toDeg(gaze.yaw),
        toDeg(gaze.pitch),
        this.cfg.calibration.screen_min_halfwidth_deg,
      );
      decision = {
        ...base,
        label: zone.label,
        state,
        p_camera: zone.p_camera,
        p_bottom: zone.p_bottom,
        face_valid: true,
        probs,
        uncertain_reason: reason ?? zone.reason,
        direction: state === 'OTHER' ? directionOf(offset) : null,
        offset_deg: offset,
        aim_deg: aimOffset(model, toDeg(gaze.yaw), toDeg(gaze.pitch)),
        condition,
      };
    }
    if (condition?.severe) {
      // The calibrated person is not the one being measured: report "could not tell".
      decision = {
        ...decision,
        label: 'UNCERTAIN',
        state: 'UNCERTAIN',
        face_valid: false,
        direction: null,
        uncertain_reason: condition.issues[0] ?? 'FACE_LOST',
      };
    }
    this.#feedReanchor(obs, gaze);
    return decision;
  }

  dispose(): void {
    if (this.#disposed) return;
    this.#disposed = true;
    this.#deps.detector.close();
  }

  // ------------------------------------------------------ gauge flow (demo)

  /**
   * Begin one gauge-driven cue.  The head must point the cue's way from the head
   * circle's centre (else the screen-centre look); `checkDirection = false` drops
   * that check for this attempt -- for someone whose head barely moves, after
   * the cue has timed out on it.
   */
  startCue(cue: Cue, tMs: number, checkDirection = true): void {
    this.#assertOpen();
    if (this.#model) throw new Error('already calibrated; call resetCalibration() first');
    this.#samples = this.#samples.filter((s) => s.cue !== cue);
    this.#cue = cue;
    this.#gauge.setDirectionReference(checkDirection ? this.#cueDirectionReference(cue) : null);
    let target: number | null = null;
    if (cue === 'SCREEN') {
      this.#baselineCheck = null;
      this.#confirming = checkDirection && this.#canConfirmBaseline();
      if (this.#confirming) target = this.cfg.calibration.cue_confirm_frames;
    }
    this.#gauge.start(cue, tMs, target);
  }

  /** A head circle centre exists for the screen-centre look to confirm. */
  #canConfirmBaseline(): boolean {
    return (
      this.cfg.calibration.cue_direction_gate &&
      this.#sweep.status.neutral_deg !== null &&
      this.#neutralFrames.length >= this.cfg.sweep.neutral_frames
    );
  }

  /**
   * Where the cue directions are read from (`_cue_direction_reference`): the
   * screen-centre look against the head circle's centre; the lens and script
   * looks from the screen-centre posture once measured, else the circle's centre.
   */
  #cueDirectionReference(cue: Cue): [number, number] | null {
    const n = this.#sweep.status.neutral_deg;
    const neutral: [number, number] | null = n ? [n[0], n[1]] : null;
    if (cue === 'SCREEN') return neutral;
    return this.#screenPosture() ?? neutral;
  }

  /** Median head `[yaw, pitch]` of the screen-centre samples, once there are enough. */
  #screenPosture(): [number, number] | null {
    const screen = this.#samples.filter((s) => s.cue === 'SCREEN');
    if (screen.length < this.cfg.calibration.min_samples_per_class) return null;
    return [median(screen.map((s) => s.headYawDeg)), median(screen.map((s) => s.headPitchDeg))];
  }

  /**
   * Once the screen-centre look is done (`_settle_baseline`): close to the head
   * circle's centre, its frames join the screen-centre samples and the look
   * ends; else the look goes on to a full measurement of the new posture.
   */
  #settleBaseline(): void {
    const n = this.#sweep.status.neutral_deg;
    if (this.#cue !== 'SCREEN' || this.#baselineCheck !== null || !n) return;
    if (this.#gauge.status.state !== 'DONE') return;
    const screen = this.#samples.filter((s) => s.cue === 'SCREEN');
    if (!screen.length) return;
    const my = median(screen.map((s) => s.headYawDeg));
    const mp = median(screen.map((s) => s.headPitchDeg));
    const shift = Math.hypot(my - n[0], mp - n[1]);
    const cal = this.cfg.calibration;
    const close = shift <= cal.cue_confirm_deg;
    if (this.#confirming && close) {
      let seeded = 0;
      for (const { obs, gaze } of this.#neutralFrames) {
        if (!gaze || !(gaze.confidence >= cal.min_sample_confidence)) continue;
        this.#samples.push(sampleOf('SCREEN', { obs, gaze }));
        this.#scene.add(obs, 'SCREEN');
        seeded += 1;
      }
      this.#baselineCheck = { confirmed: true, shift_deg: shift, seeded };
    } else if (this.#confirming) {
      // The presenter moved since the circle: measure this posture in full.
      this.#gauge.extend(cal.target_good_frames);
      this.#baselineCheck = { confirmed: false, shift_deg: shift, seeded: 0 };
    } else {
      this.#baselineCheck = { confirmed: close, shift_deg: shift, seeded: 0 };
    }
    this.#confirming = false;
  }

  /** How the screen-centre look compared with the head circle's centre (null before it ends). */
  get baselineCheck(): BaselineCheck | null {
    return this.#baselineCheck;
  }

  offerCalibrationFrame(frame: F, tMs: number): { status: GaugeStatus; observation: Observation } {
    const observation = this.observe(frame, tMs);
    if (!this.#cue) return { status: this.#gauge.status, observation };
    const gaze = GazeEngine.gazeOf(observation);
    const [accepted] = this.#gauge.offer(observation, gaze, observation.tMs);
    if (accepted && gaze) {
      const sample = sampleOf(this.#cue, { obs: observation, gaze });
      this.#samples.push(sample);
      this.#scene.add(observation, this.#cue);
    }
    this.#settleBaseline();
    return { status: this.#gauge.status, observation };
  }

  /** Placement from the CAMERA and SCREEN cues collected so far. */
  estimatePlacement(): PlacementResultDict | null {
    const deg = (cue: Cue) =>
      this.#samples.filter((s) => s.cue === cue).map((s) => [s.yawDeg, s.pitchDeg] as const);
    const result = placementFromSamples(
      deg('CAMERA'),
      deg('SCREEN'),
      this.cfg.calibration,
      this.cfg.placement,
    );
    if (result) this.#placement = result;
    return result;
  }

  finishCalibration(): CalibrationOutput {
    this.#cue = null;
    const out = this.#fit(this.#samples, this.#scene.build());
    const placement = this.estimatePlacement();
    if (placement) out.quality.placement = { ...placement };
    return out;
  }

  resetCalibration(): void {
    this.#model = null;
    this.#monitor = null;
    this.#quality = null;
    this.#samples = [];
    this.#scene.reset();
    this.#cue = null;
    this.#placement = null;
    this.#placementCamera = [];
    this.#placementScreen = [];
    this.#reanchor = this.#idleReanchor();
    this.#confirming = false;
    this.#baselineCheck = null;
  }

  get calibrationCounts(): Record<Cue, number> {
    const n = (c: Cue) => this.#samples.filter((s) => s.cue === c).length;
    return { CAMERA: n('CAMERA'), SCREEN: n('SCREEN'), BOTTOM: n('BOTTOM') };
  }

  // ------------------------------------------------------------ re-anchor

  beginReanchor(tMs: number): ReanchorStatus {
    if (!this.#model) throw new Error('beginReanchor() before calibration');
    this.#reanchor = {
      state: 'COLLECTING',
      collected: 0,
      target: this.cfg.calibration.reanchor_frames,
      shift_deg: null,
      reason: null,
    };
    this.#reanchorStarted = tMs;
    this.#reanchorObs = [];
    this.#reanchorGaze = [];
    return { ...this.#reanchor };
  }

  get reanchorStatus(): ReanchorStatus {
    return { ...this.#reanchor };
  }

  get monitorScene(): SceneBaseline | null {
    return this.#monitor?.scene ?? null;
  }

  // ------------------------------------------------------------ internals

  #fit(samples: Sample[], scene: SceneBaseline | null): CalibrationOutput {
    const { model, quality } = fitReference(samples, this.cfg.calibration);
    this.#quality = quality;
    if (!model) return { quality, model: null };
    const stored: StoredModel = { ...model, scene, configHash: CONFIG_HASH };
    this.calibrate(stored);
    return { quality, model: stored };
  }

  #batch(frames: readonly F[]): Measured[] {
    return frames.map((frame) => {
      const obs = this.observe(frame, this.#clock + BATCH_STEP_MS);
      return { obs, gaze: GazeEngine.gazeOf(obs) };
    });
  }

  #usable(measured: Measured[]): (Measured & { gaze: GazeEstimate })[] {
    const floor = this.cfg.calibration.min_sample_confidence;
    return measured.filter(
      (m): m is Measured & { gaze: GazeEstimate } => m.gaze !== null && m.gaze.confidence >= floor,
    );
  }

  /** Batch counterpart of the gauge's OUTLIER rule: drop frames that looked elsewhere. */
  #dropGlances<T extends Measured & { gaze: GazeEstimate }>(rows: T[]): T[] {
    if (rows.length < 4) return rows;
    const cal = this.cfg.calibration;
    const keep = new Array<boolean>(rows.length).fill(true);
    for (const k of [0, 1] as const) {
      const col = rows.map((r) => degOf(r)[k]);
      const centre = median(col);
      const scale = Math.max(
        MAD_TO_SIGMA * median(col.map((v) => Math.abs(v - centre))),
        cal.sigma_min_deg,
      );
      col.forEach((v, i) => {
        if (Math.abs(v - centre) > cal.outlier_k * scale) keep[i] = false;
      });
    }
    return rows.filter((_, i) => keep[i]);
  }

  /** A cue without one usable frame: NOT_ENOUGH_SAMPLES, never an exception. */
  #emptyPlacement(nCamera: number, nScreen: number): PlacementResultDict {
    const nothing = { ...this.cfg.placement, min_samples_per_target: Number.POSITIVE_INFINITY };
    return placementFromAnchors([0, 0], [0, 0], nCamera, nScreen, [1, 1], nothing);
  }

  #feedReanchor(obs: Observation, gaze: GazeEstimate | null): void {
    const st = this.#reanchor;
    if (st.state !== 'COLLECTING' || !this.#model) return;
    const cal = this.cfg.calibration;
    if (obs.tMs - this.#reanchorStarted > cal.reanchor_timeout_ms) {
      this.#reanchor = { ...st, state: 'TIMED_OUT', reason: 'NOT_ENOUGH_FRAMES' };
      return;
    }
    if (!obs.faceValid || !gaze || gaze.confidence < cal.min_sample_confidence) return;
    this.#reanchorObs.push(obs);
    this.#reanchorGaze.push([toDeg(gaze.yaw), toDeg(gaze.pitch)]);
    const collected = this.#reanchorGaze.length;
    if (collected < st.target) {
      this.#reanchor = { ...st, collected };
      return;
    }
    const med: [number, number] = [
      median(this.#reanchorGaze.map((g) => g[0])),
      median(this.#reanchorGaze.map((g) => g[1])),
    ];
    const anchor = this.#model.anchors.CAMERA!;
    const shift: [number, number] = [med[0] - anchor[0], med[1] - anchor[1]];
    if (Math.max(Math.abs(shift[0]), Math.abs(shift[1])) > cal.reanchor_max_shift_deg) {
      this.#reanchor = {
        ...st,
        state: 'REJECTED',
        collected,
        shift_deg: shift,
        reason: 'SHIFT_TOO_LARGE',
      };
      return;
    }
    const head: [number, number] = [
      median(this.#reanchorObs.map((o) => toDeg(o.headPose.yaw))),
      median(this.#reanchorObs.map((o) => toDeg(o.headPose.pitch))),
    ];
    this.#model = { ...shiftedModel(this.#model, shift[0], shift[1]), headBaseline: head };
    if (this.#monitor) {
      const acc = new SceneBaselineAccumulator();
      for (const o of this.#reanchorObs) acc.add(o);
      const fresh = acc.build();
      if (fresh)
        this.#monitor.rebaseline({
          ...fresh,
          headPoses: shiftedHeadPoses(this.#monitor.scene, fresh.head),
        });
    }
    this.#reanchor = { ...st, state: 'DONE', collected, shift_deg: shift };
  }

  #idleReanchor(): ReanchorStatus {
    return {
      state: 'IDLE',
      collected: 0,
      target: this.cfg.calibration.reanchor_frames,
      shift_deg: null,
      reason: null,
    };
  }

  #assertOpen(): void {
    if (this.#disposed) throw new Error('GazeEngine is disposed');
  }
}

function degOf(m: { gaze: GazeEstimate }): [number, number] {
  return [toDeg(m.gaze.yaw), toDeg(m.gaze.pitch)];
}

function sampleOf(cue: Cue, m: { obs: Observation; gaze: GazeEstimate }): Sample {
  return {
    cue,
    yawDeg: toDeg(m.gaze.yaw),
    pitchDeg: toDeg(m.gaze.pitch),
    headYawDeg: toDeg(m.obs.headPose.yaw),
    headPitchDeg: toDeg(m.obs.headPose.pitch),
  };
}

/** The classifier part of a stored model (drops `scene` and `configHash`). */
function stripStored(m: ReferenceModelData): ReferenceModelData {
  return {
    schema: m.schema,
    anchors: m.anchors,
    counts: m.counts,
    sigma: m.sigma,
    boxes: m.boxes,
    logPriors: m.logPriors,
    logOtherDensity: m.logOtherDensity,
    headBaseline: m.headBaseline,
    classes: m.classes,
    spreads: m.spreads,
  };
}
