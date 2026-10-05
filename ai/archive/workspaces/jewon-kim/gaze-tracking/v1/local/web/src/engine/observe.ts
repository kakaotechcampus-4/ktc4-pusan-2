/**
 * One analysed frame -> `Observation` (port of `PreprocessPipeline._observe`).
 *
 * The validity gates run in the Python order: LOW_FACE_CONFIDENCE ->
 * FACE_TOO_SMALL -> OUT_OF_FRAME -> EYES_CLOSED -> CROP_FAILED.  Brightness
 * comes from a `LumaSampler` the caller provides (the worker samples a small
 * OffscreenCanvas); without one the brightness fields stay 0 ("not measured").
 *
 * Not ported: the solvePnP head-pose fallback.  MediaPipe returns a
 * transformation matrix for every detected face; a face without one gets an
 * unmeasured pose, which the head-pose backbone refuses (BACKBONE_FAILED)
 * instead of reading as frontal.
 */
import type { PreprocessConfig } from './config';
import {
  bboxArea,
  bboxFromLandmarks,
  eyeAspectRatio,
  eyeCroppable,
  faceGuide,
  inBoundsFraction,
  irisDiameterPx,
  secondFaceRatio,
  selectMainFace,
  type Landmark,
} from './geometry';
import { headPoseFromMatrix, UNMEASURED_POSE } from './headpose';
import type { Bbox, FaceScene, FrameQuality, InvalidReason, Observation, Point } from './types';

/** The parts of a MediaPipe `FaceLandmarkerResult` the engine reads. */
export interface LandmarkerResult {
  faceLandmarks: readonly (readonly Landmark[])[];
  facialTransformationMatrixes?: readonly { data: ArrayLike<number> }[];
}

/** Mean luma (0-255) of the face box and of everything outside it. */
export interface LumaSampler {
  measure(
    faceBbox: Bbox,
    imageSize: readonly [number, number],
  ): { face: number; background: number } | null;
}

const EMPTY_QUALITY: FrameQuality = {
  leftEyeOpenness: 0,
  rightEyeOpenness: 0,
  minEyeOpenness: 0,
  landmarkVisibility: 0,
  faceAreaRatio: 0,
  touchesBorder: false,
  faceBrightness: 0,
  backgroundBrightness: 0,
  backlightRatio: 0,
};

export function observe(
  result: LandmarkerResult,
  imageSize: readonly [number, number],
  tMs: number,
  cfg: PreprocessConfig,
  options: { luma?: LumaSampler | null; hint?: Point | null } = {},
): Observation {
  const faces = result.faceLandmarks ?? [];
  const bboxes = faces.map((lm) => bboxFromLandmarks(lm, imageSize));
  const main = selectMainFace(bboxes, imageSize, options.hint ?? null);
  if (main === null) {
    return {
      tMs,
      faceValid: false,
      invalidReason: 'NO_FACE',
      presence: 0,
      headPose: UNMEASURED_POSE,
      quality: EMPTY_QUALITY,
      faceBbox: null,
      imageSize,
      scene: { nFaces: 0, secondFaceAreaRatio: 0, irisDiameterPx: 0 },
      guide: null,
    };
  }

  const lm = faces[main]!;
  const bbox = bboxes[main]!;
  const others = bboxes.filter((_, i) => i !== main);
  const scene: FaceScene = {
    nFaces: faces.length,
    secondFaceAreaRatio: secondFaceRatio(bbox, others, imageSize, cfg.min_face_area_ratio),
    irisDiameterPx: irisDiameterPx(lm, imageSize),
  };

  const matrix = result.facialTransformationMatrixes?.[main];
  const headPose = matrix ? headPoseFromMatrix(matrix.data) : UNMEASURED_POSE;

  const left = eyeAspectRatio(lm, 'left', imageSize);
  const right = eyeAspectRatio(lm, 'right', imageSize);
  const quality: FrameQuality = {
    ...EMPTY_QUALITY,
    leftEyeOpenness: left,
    rightEyeOpenness: right,
    minEyeOpenness: Math.min(left, right),
    landmarkVisibility: inBoundsFraction(lm),
  };
  const [width, height] = imageSize;
  if (bbox[2] >= 1 && bbox[3] >= 1) {
    const tol = cfg.border_tolerance_px;
    quality.faceAreaRatio = (bbox[2] * bbox[3]) / Math.max(1, width * height);
    quality.touchesBorder =
      tol >= 0 &&
      (bbox[0] <= tol ||
        bbox[1] <= tol ||
        bbox[0] + bbox[2] >= width - tol ||
        bbox[1] + bbox[3] >= height - tol);
    const luma = options.luma?.measure(bbox, imageSize);
    if (luma) {
      quality.faceBrightness = luma.face;
      quality.backgroundBrightness = Math.max(0, luma.background);
      quality.backlightRatio = luma.face > 1e-6 ? quality.backgroundBrightness / luma.face : 0;
    }
  }

  const presence = inBoundsFraction(lm);
  let reason: InvalidReason | null = null;
  if (presence < cfg.min_face_confidence) reason = 'LOW_FACE_CONFIDENCE';
  else if (quality.faceAreaRatio < cfg.min_face_area_ratio) reason = 'FACE_TOO_SMALL';
  else if (quality.touchesBorder) reason = 'OUT_OF_FRAME';
  else if (quality.minEyeOpenness < cfg.min_eye_openness) reason = 'EYES_CLOSED';
  else if (
    bbox[2] < 2 ||
    bbox[3] < 2 ||
    (!eyeCroppable(lm, 'left', imageSize) && !eyeCroppable(lm, 'right', imageSize))
  ) {
    reason = 'CROP_FAILED';
  }

  return {
    tMs,
    faceValid: reason === null,
    invalidReason: reason,
    presence,
    headPose,
    quality,
    faceBbox: bbox,
    imageSize,
    scene,
    guide: faceGuide(lm),
  };
}

/** Normalised centre of the observation's face, the hint for the next frame. */
export function faceCentre(obs: Observation): Point | null {
  if (!obs.faceBbox || bboxArea(obs.faceBbox) <= 0) return null;
  const [w, h] = obs.imageSize;
  return [
    (obs.faceBbox[0] + obs.faceBbox[2] / 2) / Math.max(w, 1),
    (obs.faceBbox[1] + obs.faceBbox[3] / 2) / Math.max(h, 1),
  ];
}
