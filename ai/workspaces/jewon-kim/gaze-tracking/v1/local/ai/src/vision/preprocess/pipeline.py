"""Frame -> ``FrameObservation``: the whole of doc 3-1 in one object.

Invalid frames are still fully populated.  doc 3-1's last bullet asks for a
reason code, not for a hole: the landmarks, head pose and quality of a frame
whose eyes are closed are exactly what the doc 19 buckets and the doc 5-2
calibration hints need in order to explain the failure to the user.
"""

from __future__ import annotations

import time
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from ..config import VisionConfig
from ..schemas import FaceScene, FrameObservation, FrameQuality, HeadPose, InvalidReason
from .crops import crop_eyes, crop_face, face_bbox_from_landmarks, frame_quality, iris_diameter_px, to_pixels
from .headpose import head_pose_from_landmarks, head_pose_from_matrix
from .landmarker import FaceLandmarkerWrapper, LandmarkResult, _validate_frame

#: A normalised (x, y) point in the frame -- where the main face was last seen.
FaceHint = Tuple[float, float]


class PreprocessPipeline:
    """Landmarker + crops + head pose for one analysed frame (doc 3-1)."""

    def __init__(self, cfg: VisionConfig) -> None:
        self.cfg = cfg
        self.pre = cfg.preprocess
        self.landmarker = FaceLandmarkerWrapper(cfg.preprocess)

    def process_bgr(
        self,
        bgr: np.ndarray,
        frame_id: int,
        t_ms: int,
        *,
        main_face_hint: Optional[FaceHint] = None,
    ) -> FrameObservation:
        """Analyse an OpenCV BGR frame (what a ``VideoCapture`` hands you).

        Validated BEFORE the colour conversion, not after: ``cv2.cvtColor``
        accepts a grayscale frame and a BGRA one without complaining, so a
        wrong-shaped frame used to reach the backbone as a valid observation.

        ``main_face_hint`` is the normalised centre of the face being followed;
        with it the face nearest that point is analysed, without it the largest
        (see :func:`select_main_face`).
        """
        started = time.perf_counter()
        _validate_frame(bgr)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return self._observe(rgb, frame_id, t_ms, started, main_face_hint)

    def process_rgb(
        self,
        rgb: np.ndarray,
        frame_id: int,
        t_ms: int,
        *,
        main_face_hint: Optional[FaceHint] = None,
    ) -> FrameObservation:
        """Analyse an already-RGB frame.

        Same guard as ``process_bgr`` and as ``landmarker.detect``: one shape
        and dtype contract, raised as the same ``ValueError`` at every door.
        """
        started = time.perf_counter()
        _validate_frame(rgb)
        return self._observe(rgb, frame_id, t_ms, started, main_face_hint)

    def close(self) -> None:
        self.landmarker.close()

    def __enter__(self) -> "PreprocessPipeline":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _observe(
        self,
        rgb: np.ndarray,
        frame_id: int,
        t_ms: int,
        started: float,
        main_face_hint: Optional[FaceHint] = None,
    ) -> FrameObservation:
        height, width = rgb.shape[:2]
        image_size: Tuple[int, int] = (width, height)

        faces = self.landmarker.detect_all(rgb, t_ms=t_ms)
        bboxes = [face_bbox_from_landmarks(face.landmarks, image_size) for face in faces]
        main = select_main_face(bboxes, image_size, main_face_hint)
        if main is None:
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
                scene=FaceScene(n_faces=0),
            )

        result = faces[main]
        scene = _scene(main, bboxes, result.landmarks, image_size, float(self.pre.min_face_area_ratio))
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
            scene=scene,
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


def _bbox_area(bbox: Tuple[int, int, int, int]) -> float:
    return float(max(0, bbox[2]) * max(0, bbox[3]))


def _bbox_centre(bbox: Tuple[int, int, int, int], image_size: Tuple[int, int]) -> FaceHint:
    width, height = float(image_size[0]), float(image_size[1])
    return (
        (bbox[0] + bbox[2] / 2.0) / max(width, 1.0),
        (bbox[1] + bbox[3] / 2.0) / max(height, 1.0),
    )


def select_main_face(
    bboxes: Sequence[Tuple[int, int, int, int]],
    image_size: Tuple[int, int],
    hint: Optional[FaceHint] = None,
) -> Optional[int]:
    """Index of the face to analyse, or ``None`` when there is no usable face.

    Without a hint: the largest face -- the presenter sits closest to their own
    laptop.  With one: the face whose centre is nearest the hint (ties go to
    the larger face), so someone leaning in from the side cannot take over the
    measurement just by being momentarily bigger.  A face with an empty bbox
    (non-finite landmarks) only wins when it is the only thing detected; it is
    then still analysed, and reported FACE_TOO_SMALL, exactly as before multiple
    faces were considered.
    """
    if not bboxes:
        return None
    candidates = [i for i, bbox in enumerate(bboxes) if _bbox_area(bbox) > 0.0]
    if not candidates:
        return 0
    if hint is None or not all(np.isfinite(hint)):
        return max(candidates, key=lambda i: (_bbox_area(bboxes[i]), -i))

    def distance(i: int) -> float:
        cx, cy = _bbox_centre(bboxes[i], image_size)
        return float(np.hypot(cx - float(hint[0]), cy - float(hint[1])))

    return min(candidates, key=lambda i: (round(distance(i), 6), -_bbox_area(bboxes[i]), i))


def _inside(point: Tuple[float, float], bbox: Tuple[int, int, int, int]) -> bool:
    x, y, w, h = bbox
    return x <= point[0] <= x + w and y <= point[1] <= y + h


def second_face_ratio(
    main_bbox: Tuple[int, int, int, int],
    others: Sequence[Tuple[int, int, int, int]],
    image_size: Tuple[int, int],
    min_area_ratio: float,
) -> float:
    """Area of the largest *other person's* face over the main face's; 0 when none.

    Only a distinct face counts.  The main face found a second time -- either
    box's centre inside the other box; MediaPipe sometimes returns one face
    twice, more often when it is small -- is the same person.  A find smaller
    than the face detector's own floor (``min_area_ratio`` of the frame, where
    most "faces" are false finds in the background) is not taken for one.
    """
    main_area = _bbox_area(main_bbox)
    if main_area <= 0.0:
        return 0.0
    floor = float(min_area_ratio) * float(image_size[0]) * float(image_size[1])
    mx, my, mw, mh = main_bbox
    main_centre = (mx + mw / 2.0, my + mh / 2.0)
    best = 0.0
    for bbox in others:
        area = _bbox_area(bbox)
        if area <= 0.0 or area < floor:
            continue
        x, y, w, h = bbox
        if _inside((x + w / 2.0, y + h / 2.0), main_bbox) or _inside(main_centre, bbox):
            continue
        best = max(best, area / main_area)
    return best


def _scene(
    main: int,
    bboxes: List[Tuple[int, int, int, int]],
    landmarks: np.ndarray,
    image_size: Tuple[int, int],
    min_area_ratio: float = 0.0,
) -> FaceScene:
    others = [b for i, b in enumerate(bboxes) if i != main]
    second = second_face_ratio(bboxes[main], others, image_size, min_area_ratio)
    return FaceScene(
        n_faces=len(bboxes),
        second_face_area_ratio=float(second),
        iris_diameter_px=iris_diameter_px(landmarks, image_size),
        face_bboxes=[tuple(int(v) for v in bboxes[main])]
        + [tuple(int(v) for v in b) for i, b in enumerate(bboxes) if i != main],
    )


def face_centre(obs: FrameObservation) -> Optional[FaceHint]:
    """Normalised centre of the observation's main face, or ``None``."""
    if obs.face_bbox is None or obs.image_size is None or _bbox_area(obs.face_bbox) <= 0.0:
        return None
    return _bbox_centre(obs.face_bbox, obs.image_size)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
