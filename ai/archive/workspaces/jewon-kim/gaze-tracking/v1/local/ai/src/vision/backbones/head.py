"""Head direction as the gaze estimate -- the default backbone.

The labels this module serves (lens, screen, script, elsewhere) are judged from
where the face points.  On a laptop webcam the eye-reading backbones in
``vision.eye`` could not see vertical eye movement with the head held still
(their measurements are described in that package's backbones and in the v1
README), while the head pose separated the same targets whenever the head
moved toward them -- which is what a presenter does when reading a script at
the bottom of the screen.  A per-user calibration still anchors each target, so
how far a given person turns their head is learned, not assumed.

The estimate is the head pose itself, in the ``schemas`` convention the head
pose already uses: yaw > 0 turns the face toward image right, pitch > 0 lifts
the chin.  That is exactly what an eye backbone returns for a zero eye-in-head
offset, so every downstream stage reads it unchanged.

``confidence`` is the fraction of landmarks inside the frame: a face sliding
out of the picture is the one thing that degrades a head pose without
invalidating the frame.  A head pose that was never measured (a failed solve)
raises, so the session books it as a backbone failure instead of a frontal
face.
"""

from __future__ import annotations

import math
import time
from typing import Dict, Optional, Tuple

import numpy as np

from vision.backbones.base import GazeBackbone
from vision.backbones.registry import register
from vision.config import BackboneConfig
from vision.preprocess.landmarker import in_bounds_fraction
from vision.schemas import GazeVector, HeadPose


@register("head_pose")
class HeadPoseBackbone(GazeBackbone):
    """Gaze = the direction the face points (no eye reading)."""

    version = "1.0.0"
    uses_eyes = False

    def __init__(self, cfg: Optional[BackboneConfig] = None) -> None:
        self.cfg = cfg if cfg is not None else BackboneConfig(name="head_pose")

    def predict(
        self,
        face_crop: Optional[np.ndarray],
        left_eye_crop: Optional[np.ndarray],
        right_eye_crop: Optional[np.ndarray],
        head_pose: Optional[HeadPose],
        *,
        landmarks: Optional[np.ndarray] = None,
        blendshapes: Optional[Dict[str, float]] = None,
        image_size: Optional[Tuple[int, int]] = None,
    ) -> GazeVector:
        t0 = time.perf_counter()
        if head_pose is None:
            raise ValueError("head_pose backbone needs a head pose")
        yaw, pitch = float(head_pose.yaw), float(head_pose.pitch)
        # A failed PnP solve keeps zero angles and flags itself with an infinite
        # reprojection error; reading those zeros as "facing the lens" would be
        # the most confident wrong answer available.
        if not (math.isfinite(yaw) and math.isfinite(pitch)) or not math.isfinite(
            float(head_pose.reprojection_error)
        ):
            raise ValueError("head pose was not measured on this frame")

        confidence = 1.0
        if landmarks is not None:
            arr = np.asarray(landmarks, dtype=np.float64)
            if arr.ndim == 2 and arr.shape[0] > 0 and arr.shape[1] >= 2:
                confidence = in_bounds_fraction(arr)
        return GazeVector(
            gaze_yaw=yaw,
            gaze_pitch=pitch,
            confidence=float(np.clip(confidence, 0.0, 1.0)),
            backbone=self.name,
            inference_ms=self._elapsed_ms(t0),
        )
