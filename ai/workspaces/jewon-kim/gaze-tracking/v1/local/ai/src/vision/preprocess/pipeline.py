"""Frame -> ``FrameObservation``: the whole of doc 3-1 in one object.

Invalid frames are still fully populated.  doc 3-1's last bullet asks for a
reason code, not for a hole: the landmarks, head pose and quality of a frame
whose eyes are closed are exactly what the doc 19 buckets and the doc 5-2
calibration hints need in order to explain the failure to the user.
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

import cv2
import numpy as np

from ..config import VisionConfig
from ..schemas import FrameObservation, FrameQuality, HeadPose, InvalidReason
from .crops import crop_eyes, crop_face, frame_quality, to_pixels
from .headpose import head_pose_from_landmarks, head_pose_from_matrix
from .landmarker import FaceLandmarkerWrapper, LandmarkResult, _validate_frame


class PreprocessPipeline:
    """Landmarker + crops + head pose for one analysed frame (doc 3-1)."""

    def __init__(self, cfg: VisionConfig) -> None:
        self.cfg = cfg
        self.pre = cfg.preprocess
        self.landmarker = FaceLandmarkerWrapper(cfg.preprocess)

    def process_bgr(self, bgr: np.ndarray, frame_id: int, t_ms: int) -> FrameObservation:
        """Analyse an OpenCV BGR frame (what a ``VideoCapture`` hands you).

        Validated BEFORE the colour conversion, not after: ``cv2.cvtColor``
        accepts a grayscale frame and a BGRA one without complaining, so a
        wrong-shaped frame used to reach the backbone as a valid observation.
        """
        started = time.perf_counter()
        _validate_frame(bgr)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return self._observe(rgb, frame_id, t_ms, started)

    def process_rgb(self, rgb: np.ndarray, frame_id: int, t_ms: int) -> FrameObservation:
        """Analyse an already-RGB frame.

        Same guard as ``process_bgr`` and as ``landmarker.detect``: one shape
        and dtype contract, raised as the same ``ValueError`` at every door.
        """
        started = time.perf_counter()
        _validate_frame(rgb)
        return self._observe(rgb, frame_id, t_ms, started)

    def close(self) -> None:
        self.landmarker.close()

    def __enter__(self) -> "PreprocessPipeline":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _observe(
        self, rgb: np.ndarray, frame_id: int, t_ms: int, started: float
    ) -> FrameObservation:
        height, width = rgb.shape[:2]
        image_size: Tuple[int, int] = (width, height)

        result = self.landmarker.detect(rgb, t_ms=t_ms)
        if result is None:
            return FrameObservation(
                frame_id=int(frame_id),
                t_ms=int(t_ms),
                face_confidence=0.0,
                face_valid=False,
                # Every other FrameQuality default already reads as "nothing
                # seen", but landmark_visibility defaults to 1.0 -- the right
                # default for a rehydrated row that never stored the column,
                # and a lie here.  ``in_bounds_fraction`` of no landmarks is
                # 0.0, and that is the function this field is defined by, so
                # leaving the default in place fed a 1.0 into the doc 19
                # visibility buckets on the one branch that saw no face at all.
                quality=FrameQuality(landmark_visibility=0.0),
                image_size=image_size,
                invalid_reason=InvalidReason.NO_FACE.value,
                preprocess_ms=_elapsed_ms(started),
            )

        head_pose = self._head_pose(result, image_size)
        face_crop, bbox = crop_face(rgb, result.landmarks, self.pre)
        left_eye, right_eye = crop_eyes(rgb, result.landmarks, self.pre)
        quality = frame_quality(
            rgb, result.landmarks, bbox, image_size, self.pre.border_tolerance_px
        )
        reason = self._invalid_reason(result, quality, face_crop, left_eye, right_eye)

        return FrameObservation(
            frame_id=int(frame_id),
            t_ms=int(t_ms),
            face_confidence=float(result.presence),
            face_valid=reason is None,
            head_pose=head_pose,
            quality=quality,
            face_crop=face_crop,
            left_eye_crop=left_eye,
            right_eye_crop=right_eye,
            landmarks=result.landmarks,
            blendshapes=result.blendshapes,
            face_bbox=bbox,
            image_size=image_size,
            invalid_reason=reason,
            preprocess_ms=_elapsed_ms(started),
        )

    def _head_pose(
        self, result: LandmarkResult, image_size: Tuple[int, int]
    ) -> HeadPose:
        """Prefer MediaPipe's matrix; fall back to PnP on the same landmarks.

        The fallback is calibrated against the matrix path (see
        ``headpose.PNP_MODEL_POINTS``) so a frame that switches paths does not
        step the head-pose features, but the matrix is the better estimate --
        it comes from the mesh fit rather than from nine of its points.
        """
        if result.transform_matrix is not None:
            return head_pose_from_matrix(result.transform_matrix, image_size)
        return head_pose_from_landmarks(
            to_pixels(result.landmarks, image_size), image_size
        )

    def _invalid_reason(
        self,
        result: LandmarkResult,
        quality: FrameQuality,
        face_crop: Optional[np.ndarray],
        left_eye: Optional[np.ndarray],
        right_eye: Optional[np.ndarray],
    ) -> Optional[str]:
        """First matching reason in doc 3-1's order, or ``None`` if usable."""
        if result.presence < self.pre.min_face_confidence:
            return InvalidReason.LOW_FACE_CONFIDENCE.value
        if quality.face_area_ratio < self.pre.min_face_area_ratio:
            return InvalidReason.FACE_TOO_SMALL.value
        if quality.touches_border:
            return InvalidReason.OUT_OF_FRAME.value
        if quality.min_eye_openness < self.pre.min_eye_openness:
            return InvalidReason.EYES_CLOSED.value
        # One missing eye crop is survivable -- the face-crop backbones (doc 3-2
        # L2CS, GazeTR) never look at the eye crops -- but losing the face crop
        # or both eyes leaves nothing for any backbone to run on.
        if face_crop is None or (left_eye is None and right_eye is None):
            return InvalidReason.CROP_FAILED.value
        return None


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
