/**
 * Shared engine types.  Names follow the Python package (`vision.schemas`);
 * fields are camelCase here, and only the objects that cross into the frontend
 * adapter keep Python's snake_case `to_dict()` keys (see `contract.ts`).
 *
 * Sign convention (same as `vision/schemas.py`): yaw > 0 turns the face (or
 * the gaze) toward image right, pitch > 0 is up.  Angles are radians unless the
 * name says `Deg`.
 */

/** Calibration cue: what the user was asked to look at. */
export type Cue = 'CAMERA' | 'SCREEN' | 'BOTTOM';
/** Decided class. */
export type StateClass = 'CAMERA' | 'SCREEN' | 'BOTTOM' | 'OTHER';
/** Per-frame state, with the abstention. */
export type State = StateClass | 'UNCERTAIN';

/** Tie-break order, as `STATE_CLASSES` in Python. */
export const STATE_CLASSES: readonly StateClass[] = ['CAMERA', 'SCREEN', 'BOTTOM', 'OTHER'];
/** Calibration cue order, top to bottom on the screen. */
export const CUES: readonly Cue[] = ['CAMERA', 'SCREEN', 'BOTTOM'];

/**
 * Where an OTHER look went, from the PRESENTER's point of view (their own left
 * and right), in 45-degree sectors counter-clockwise from their right.  Also the
 * tie-break order when slices are merged.
 */
export const GAZE_DIRECTIONS = [
  'RIGHT',
  'UP_RIGHT',
  'UP',
  'UP_LEFT',
  'LEFT',
  'DOWN_LEFT',
  'DOWN',
  'DOWN_RIGHT',
] as const;
export type GazeDirection = (typeof GAZE_DIRECTIONS)[number];

/** Preprocess reasons a frame is not usable (Python `InvalidReason`). */
export type InvalidReason =
  | 'NO_FACE'
  | 'LOW_FACE_CONFIDENCE'
  | 'FACE_TOO_SMALL'
  | 'OUT_OF_FRAME'
  | 'EYES_CLOSED'
  | 'CROP_FAILED'
  | 'BACKBONE_FAILED';

export interface HeadPose {
  yaw: number;
  pitch: number;
  roll: number;
  /** `Infinity` when the pose was never measured on this frame. */
  reprojectionError: number;
  /** Camera-to-head distance from the face-mesh fit, cm (NaN or 0 when unknown). */
  depthCm: number;
}

export interface FrameQuality {
  leftEyeOpenness: number;
  rightEyeOpenness: number;
  minEyeOpenness: number;
  /** Fraction of landmarks inside the frame. */
  landmarkVisibility: number;
  faceAreaRatio: number;
  touchesBorder: boolean;
  /** Mean face luma 0-255 (0 when not measured). */
  faceBrightness: number;
  backgroundBrightness: number;
  /** Background over face luma; 0 when the face luma is 0. */
  backlightRatio: number;
}

export interface FaceScene {
  nFaces: number;
  /** Largest other face's bbox area over the main face's. */
  secondFaceAreaRatio: number;
  irisDiameterPx: number;
}

/** `[x, y, w, h]` in pixels. */
export type Bbox = readonly [number, number, number, number];
/** Normalised `[x, y]` in the raw (not mirrored) frame. */
export type Point = readonly [number, number];

/**
 * A few landmarks a UI needs to draw a face guide (oval + cross, head arrow).  Not used by
 * the engine itself, and never part of the frontend contract.
 */
export interface FaceGuide {
  forehead: Point;
  chin: Point;
  /** Image-left face side (landmark 234) and image-right side (454). */
  sideLeft: Point;
  sideRight: Point;
  /** Image-left and image-right outer eye corners (33, 263). */
  eyeLeft: Point;
  eyeRight: Point;
  /** Nose tip (landmark 1): where the head-direction arrow starts. */
  nose: Point;
}

/** Everything preprocess produces for one analysed frame (Python `FrameObservation`). */
export interface Observation {
  tMs: number;
  faceValid: boolean;
  invalidReason: InvalidReason | null;
  /** In-bounds fraction of the main face's landmarks. */
  presence: number;
  headPose: HeadPose;
  quality: FrameQuality;
  faceBbox: Bbox | null;
  /** `[width, height]` of the analysed frame. */
  imageSize: readonly [number, number];
  scene: FaceScene;
  guide: FaceGuide | null;
}

/** One accepted calibration frame, in degrees. */
export interface Sample {
  cue: Cue;
  /** Gaze = head direction for the head-pose engine. */
  yawDeg: number;
  pitchDeg: number;
  headYawDeg: number;
  headPitchDeg: number;
}

export const toDeg = (rad: number): number => (rad * 180) / Math.PI;
export const toRad = (deg: number): number => (deg * Math.PI) / 180;
