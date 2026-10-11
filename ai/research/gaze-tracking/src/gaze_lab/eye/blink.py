"""Per-user blink gate for live frames (runs after calibration).

The preprocess gate calls a frame EYES_CLOSED below one absolute eye aspect
ratio (``min_eye_openness``, 0.12).  Eye openness varies a lot between people,
so for a narrow-eyed user a half blink passes that floor and a wide-eyed user's
closing eye is caught late; a frame mid-blink then reaches the classifier with
an iris half hidden by the lid and reads as a confident downward gaze.

This gate learns the user's own open-eye openness and treats a drop below
``blink_ratio`` of it as a blink, never going below the absolute floor.  The
reference is the *smaller* of

* the running median of recent non-blink frames, and
* the median openness measured while the user read the script (BOTTOM cue).

The second term is the important one: the upper lid follows the eye down when
someone reads, so a reference taken while looking at the lens would flag every
script glance as a blink and wipe out exactly the BOTTOM frames the product is
about.  The gate stays inactive until it has a reference.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Deque, Optional

import numpy as np

from gaze_lab.config import PreprocessConfig
from gaze_lab.schemas import FrameObservation, InvalidReason


class AdaptiveBlinkGate:
    def __init__(self, cfg: PreprocessConfig) -> None:
        self.cfg = cfg
        self._recent: Deque[float] = deque(maxlen=max(1, int(cfg.blink_window_frames)))
        self._script_reference: Optional[float] = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active and bool(self.cfg.adaptive_blink)

    def calibrate(self, open_eye_values, script_values) -> None:
        """Arm the gate from the calibration's accepted frames."""
        self._recent.clear()
        for value in open_eye_values:
            if math.isfinite(value) and value > 0:
                self._recent.append(float(value))
        script = [float(v) for v in script_values if math.isfinite(v) and v > 0]
        self._script_reference = float(np.median(script)) if script else None
        self._active = True

    def reset(self) -> None:
        self._recent.clear()
        self._script_reference = None
        self._active = False

    def threshold(self) -> Optional[float]:
        """Current blink threshold, or ``None`` while the gate has no reference."""
        if not self.active or len(self._recent) < int(self.cfg.blink_min_reference_frames):
            return None
        reference = float(np.median(self._recent))
        if self._script_reference is not None:
            reference = min(reference, self._script_reference)
        if not reference > 0.0:
            return None
        return max(float(self.cfg.min_eye_openness), float(self.cfg.blink_ratio) * reference)

    def apply(self, obs: FrameObservation) -> bool:
        """Mark ``obs`` EYES_CLOSED when it is a blink; True if it did.

        Only valid frames are judged (an invalid one already has its reason),
        and only non-blink frames update the running reference, so a long
        blink cannot drag the reference down after itself.
        """
        if not obs.face_valid:
            return False
        ear = float(obs.quality.min_eye_openness)
        limit = self.threshold()
        if limit is not None and math.isfinite(ear) and ear < limit:
            obs.face_valid = False
            obs.invalid_reason = InvalidReason.EYES_CLOSED.value
            return True
        if math.isfinite(ear) and ear > 0.0 and self.active:
            self._recent.append(ear)
        return False
