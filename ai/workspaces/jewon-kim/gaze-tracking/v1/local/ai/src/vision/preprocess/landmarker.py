"""MediaPipe Face Landmarker wrapper (doc 3-1).

Everything MediaPipe-specific is confined here: the rest of the pipeline sees a
plain ``LandmarkResult`` of numpy arrays and a name->score dict.

Left / right naming (verified, do not "fix" it)
-----------------------------------------------
MediaPipe's ``FACE_LANDMARKS_LEFT_EYE`` is built from indices 362..263 and
``FACE_LANDMARKS_LEFT_IRIS`` from 474..477 (see
``mediapipe/tasks/python/vision/face_landmarker.py`` in the installed wheel).
On ``tests/fixtures/face.jpg`` those points sit at x ~ 0.55 -- the IMAGE RIGHT
half -- while the "right" sets (33..133, 469..472) sit at x ~ 0.44.  So
MediaPipe's "left" is the SUBJECT's left eye, which appears on the image right
in a non-mirrored frame, matching the ARKit convention its blendshape names
follow.  This module, ``crops`` and ``FrameObservation.left_eye_crop`` all use
that same anatomical meaning, so ``eyeLookInLeft`` and ``left_eye_crop`` always
refer to one and the same eye.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from ..config import PreprocessConfig, resolve_path

#: Landmark count of the v2 face mesh: 468 face points + 2 x 5 iris points.
NUM_LANDMARKS = 478

#: MediaPipe's own defaults.  ``PreprocessConfig.min_face_confidence`` is
#: deliberately NOT wired in here: it gates our own presence proxy downstream,
#: and pushing it into the detector would turn a weak detection into NO_FACE and
#: throw away the landmarks doc 3-1 wants kept for the failure buckets.
_MP_DETECTION_CONFIDENCE = 0.5
_MP_PRESENCE_CONFIDENCE = 0.5
_MP_TRACKING_CONFIDENCE = 0.5


@dataclass
class LandmarkResult:
    """One face as returned by MediaPipe, in plain numpy form."""

    #: (478, 3) normalised image coordinates; z is a relative depth, not metric.
    landmarks: np.ndarray
    #: 52 ARKit-style blendshape scores keyed by ``category_name``.
    blendshapes: Dict[str, float]
    #: (4, 4) face -> camera transform in MediaPipe's metric space (Y up,
    #: Z toward the viewer).  ``None`` when the task did not produce one.
    transform_matrix: Optional[np.ndarray]
    #: See ``FaceLandmarkerWrapper.detect`` for what this actually measures.
    presence: float


class FaceLandmarkerWrapper:
    """Thin, closeable wrapper around ``mpv.FaceLandmarker`` (doc 3-1)."""

    def __init__(self, cfg: PreprocessConfig) -> None:
        # Imported inside __init__ so that importing the pure-geometry modules
        # (headpose, crops) does not pay for building the MediaPipe graph.
        import mediapipe as mp
        from mediapipe.tasks import python as mpp
        from mediapipe.tasks.python import vision as mpv

        self._mp = mp
        self.cfg = cfg
        self.running_mode = str(cfg.running_mode).upper()

        model_path = resolve_path(cfg.landmarker_model_path)
        if not model_path.exists():
            raise FileNotFoundError(
                "face landmarker model not found at {p}. Download it with:\n"
                "  curl -L -o ai/models/face_landmarker.task https://storage.googleapis.com"
                "/mediapipe-models/face_landmarker/face_landmarker/float16/1/"
                "face_landmarker.task".format(p=model_path)
            )

        if self.running_mode == "IMAGE":
            mode = mpv.RunningMode.IMAGE
        elif self.running_mode == "VIDEO":
            mode = mpv.RunningMode.VIDEO
        elif self.running_mode == "LIVE_STREAM":
            # LIVE_STREAM delivers results through an async callback, which
            # cannot satisfy this class's synchronous detect() contract.
            raise NotImplementedError(
                "LIVE_STREAM running mode is not supported; use VIDEO with the "
                "capture timestamp, which keeps tracking without the callback."
            )
        else:
            raise ValueError("unknown running_mode: {!r}".format(cfg.running_mode))

        options = mpv.FaceLandmarkerOptions(
            base_options=mpp.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mode,
            num_faces=int(cfg.num_faces),
            min_face_detection_confidence=_MP_DETECTION_CONFIDENCE,
            min_face_presence_confidence=_MP_PRESENCE_CONFIDENCE,
            min_tracking_confidence=_MP_TRACKING_CONFIDENCE,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = mpv.FaceLandmarker.create_from_options(options)
        self._last_timestamp_ms = -1
        self._closed = False

    def detect(self, rgb: np.ndarray, t_ms: Optional[int] = None) -> Optional[LandmarkResult]:
        """Run the landmarker on one uint8 RGB frame (doc 3-1).

        ``presence`` is the fraction of landmarks that fall inside the image.
        MediaPipe exposes no per-face score in IMAGE mode, so this is an honest
        proxy rather than a fabricated confidence: a fully visible face scores
        1.0 and a face sliding out of frame decays toward 0.
        """
        if self._closed:
            raise RuntimeError("FaceLandmarkerWrapper is closed")
        _validate_frame(rgb)

        # mp.Image wraps the buffer without copying, so it must be contiguous.
        data = np.ascontiguousarray(rgb)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=data)

        if self.running_mode == "VIDEO":
            if t_ms is None:
                raise ValueError("VIDEO running mode requires t_ms")
            # MediaPipe rejects non-increasing timestamps outright; clamping is
            # kinder than crashing a take because two frames share a millisecond.
            stamp = max(int(t_ms), self._last_timestamp_ms + 1)
            self._last_timestamp_ms = stamp
            result = self._landmarker.detect_for_video(image, stamp)
        else:
            result = self._landmarker.detect(image)

        if not result.face_landmarks:
            return None

        points = result.face_landmarks[0]
        landmarks = np.asarray([[p.x, p.y, p.z] for p in points], dtype=np.float32)

        blendshapes: Dict[str, float] = {}
        if result.face_blendshapes:
            blendshapes = {
                c.category_name: float(c.score) for c in result.face_blendshapes[0]
            }

        matrix: Optional[np.ndarray] = None
        if result.facial_transformation_matrixes:
            matrix = np.asarray(
                result.facial_transformation_matrixes[0], dtype=np.float64
            ).reshape(4, 4)

        return LandmarkResult(
            landmarks=landmarks,
            blendshapes=blendshapes,
            transform_matrix=matrix,
            presence=in_bounds_fraction(landmarks),
        )

    def close(self) -> None:
        if not self._closed:
            self._landmarker.close()
            self._closed = True

    def __enter__(self) -> "FaceLandmarkerWrapper":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _validate_frame(frame: np.ndarray) -> None:
    """Reject anything that is not an ``HxWx3`` uint8 frame (doc 3-1).

    ``detect`` used to hold these two guards inline, and it was the pipeline's
    only input validator.  That left a hole in front of it: ``cv2.cvtColor``
    happily accepts a single-channel grayscale frame AND a 4-channel BGRA one,
    so ``PreprocessPipeline.process_bgr`` converted a wrong-shaped frame,
    detected a face in whatever came out, and reported ``face_valid=True`` with
    no record of the problem.  Both pipeline entry points call this first now,
    which is why it raises the same ``ValueError`` ``detect`` always has: the
    caller sees one contract, not two.
    """
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("expected HxWx3 uint8 pixels, got shape {}".format(frame.shape))
    if frame.dtype != np.uint8:
        raise ValueError("expected uint8 pixels, got {}".format(frame.dtype))


def in_bounds_fraction(landmarks: np.ndarray) -> float:
    """Fraction of normalised landmarks inside the image rectangle (doc 3-1).

    Shared by the presence proxy and ``FrameQuality.landmark_visibility`` so the
    two can never drift apart.
    """
    if landmarks.size == 0:
        return 0.0
    xy = np.asarray(landmarks[:, :2], dtype=np.float64)
    inside = np.all((xy >= 0.0) & (xy <= 1.0), axis=1)
    return float(np.count_nonzero(inside) / inside.size)
