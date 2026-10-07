"""Good-frame gauge for one calibration cue.

A cue used to last a fixed two seconds and keep whatever it saw; a blink, a
glance away or a head nod during those seconds went straight into the model,
and a short run of bad frames was only discovered after all the cues were done.
The gauge instead fills one step per *good* frame and the cue ends when it is
full -- the progress bar a user sees is the number of usable samples, so it
stops while something is wrong and says what.

A frame counts only when, in this order:

1. the face is valid (preprocess gates: no face, out of frame, eyes closed ...);
2. the backbone produced a finite gaze;
3. the gaze confidence clears ``min_sample_confidence``;
4. the cue has *settled* -- the first ``cue_settle_ms`` are still the saccade
   toward the new target (not counted against the user);
5. (eye-reading backbones only) the head is still within
   ``max_head_deviation_deg`` of the calibration pose -- the eyes move between
   targets, the head does not;
6. (eye-reading backbones only) it is not a blink: eye openness above
   ``blink_ratio`` x this cue's median;
7. (head-pose engine, with a reference pose) the head points the cue's way
   from the reference: up toward the lens (CAMERA), down toward the script
   (BOTTOM), near the centre (SCREEN), and never far to the side;
8. it is not a glance elsewhere: within ``outlier_k`` noise units of this
   cue's running median gaze.

With the default ``head_pose`` backbone (``eye_based=False``) steps 5 and 6 are
skipped: the user looks at each target naturally and the head movement toward
it is exactly what is being measured, while a blink does not move the head.
Step 7 is what keeps a look elsewhere -- or no head movement at all -- from
filling the gauge: the reference is the centre of the head circle
(``runtime.sweep``), or the screen-centre look when the circle was skipped,
and without either the step is off.

Bad frames *pause* the gauge; they never reset it.  The cue ends at
``target_good_frames`` (DONE) or at ``cue_timeout_ms``: DONE if it reached
``min_samples_per_class`` by then, TIMED_OUT otherwise, with the reject reason
seen most often so the retry screen can name the fix.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np

from vision.config import CalibrationConfig
from vision.schemas import FrameObservation, GazeVector, InvalidReason

#: Reject reasons the gauge adds to the preprocess ``InvalidReason`` codes.
LOW_GAZE_CONFIDENCE = "LOW_GAZE_CONFIDENCE"
SETTLING = "SETTLING"
HEAD_MOVED = "HEAD_MOVED"
BLINK = "BLINK"
OUTLIER = "OUTLIER"
NOT_ACTIVE = "NOT_ACTIVE"
#: The head did not go the cue's way from the reference pose (head-pose engine).
LOOK_HIGHER = "LOOK_HIGHER"
LOOK_LOWER = "LOOK_LOWER"
OFF_TARGET = "OFF_TARGET"

#: Reasons that are not the user's doing and do not explain a failed cue.
_NEUTRAL_REASONS = frozenset({SETTLING, NOT_ACTIVE})

#: Running statistics need a few accepted frames before they mean anything.
_MIN_FRAMES_FOR_STATS = 4
_MAD_TO_SIGMA = 1.4826

GAUGE_HINTS: Dict[str, str] = {
    InvalidReason.NO_FACE.value: "Your face is not visible. Sit in front of the camera.",
    InvalidReason.FACE_TOO_SMALL.value: "Your face is too small in the picture. Move closer.",
    InvalidReason.OUT_OF_FRAME.value: "Part of your face is outside the picture. Move to the centre.",
    InvalidReason.EYES_CLOSED.value: "Keep your eyes open while you look at the target.",
    InvalidReason.LOW_FACE_CONFIDENCE.value: "Your face is only partly visible. Face the camera.",
    InvalidReason.CROP_FAILED.value: "Your eyes are not visible. Face the camera.",
    InvalidReason.BACKBONE_FAILED.value: "The gaze could not be measured. Check the light.",
    LOW_GAZE_CONFIDENCE: "The gaze is hard to read. Improve the light on your face.",
    HEAD_MOVED: "Keep your head still and move only your eyes.",
    BLINK: "Hold your eyes open on the target for a moment.",
    OUTLIER: "Keep looking at the same point until the gauge fills.",
    LOOK_HIGHER: "Lift your head a little toward the lens.",
    LOOK_LOWER: "Lower your head a little toward the script.",
    OFF_TARGET: "Turn your head toward the target.",
}


def direction_reason(cue: Optional[str], right_deg: float, up_deg: float, cfg: CalibrationConfig) -> Optional[str]:
    """Does a head turned ``(right, up)`` degrees from the reference point the cue's way?"""
    if abs(right_deg) > float(cfg.cue_max_side_deg):
        return OFF_TARGET
    if cue == "CAMERA" and up_deg < float(cfg.cue_min_up_deg):
        return LOOK_HIGHER
    if cue == "BOTTOM" and up_deg > -float(cfg.cue_min_down_deg):
        return LOOK_LOWER
    if cue == "SCREEN" and math.hypot(right_deg, up_deg) > float(cfg.cue_screen_radius_deg):
        return OFF_TARGET
    return None


class GaugeState(str, Enum):
    IDLE = "IDLE"
    SETTLING = "SETTLING"
    COLLECTING = "COLLECTING"
    DONE = "DONE"
    TIMED_OUT = "TIMED_OUT"


@dataclass
class GaugeStatus:
    """What a calibration screen draws for the current cue."""

    cue: Optional[str]
    state: str
    good: int
    target: int
    elapsed_ms: int
    rejected: Dict[str, int] = field(default_factory=dict)
    #: The user-fixable reason seen most often (``None`` while nothing failed).
    dominant_reason: Optional[str] = None
    #: Reason of the most recent frame, for a live "what is wrong now" line.
    last_reason: Optional[str] = None

    @property
    def progress(self) -> float:
        return 0.0 if self.target <= 0 else min(1.0, self.good / float(self.target))

    @property
    def finished(self) -> bool:
        return self.state in (GaugeState.DONE.value, GaugeState.TIMED_OUT.value)

    @property
    def hint(self) -> Optional[str]:
        reason = self.last_reason if self.last_reason in GAUGE_HINTS else self.dominant_reason
        return GAUGE_HINTS.get(reason) if reason else None


class CalibrationGauge:
    """One cue at a time; ``start`` begins a cue, ``offer`` feeds it frames."""

    def __init__(
        self, cfg: CalibrationConfig, blink_ratio: float = 0.70, *, eye_based: bool = True
    ) -> None:
        self.cfg = cfg
        self.blink_ratio = float(blink_ratio)
        self.eye_based = bool(eye_based)
        self._head_reference: Optional[Tuple[float, float]] = None
        self._direction_reference: Optional[Tuple[float, float]] = None
        self._cue: Optional[str] = None
        self._state = GaugeState.IDLE
        self._target = int(cfg.target_good_frames)
        self._started_ms = 0
        self._elapsed_ms = 0
        self._gaze: List[Tuple[float, float]] = []
        self._ear: List[float] = []
        self._rejected: Counter = Counter()
        self._last_reason: Optional[str] = None

    @property
    def head_reference(self) -> Optional[Tuple[float, float]]:
        """Head ``(yaw_deg, pitch_deg)`` every cue is measured against."""
        return self._head_reference

    def reset_head_reference(self) -> None:
        self._head_reference = None

    @property
    def direction_reference(self) -> Optional[Tuple[float, float]]:
        """Head ``(yaw_deg, pitch_deg)`` the cue directions are read from (head-pose engine)."""
        return self._direction_reference

    def set_direction_reference(self, reference: Optional[Tuple[float, float]]) -> None:
        self._direction_reference = None if reference is None else (float(reference[0]), float(reference[1]))

    def start(self, cue: str, t_ms: int, target: Optional[int] = None) -> None:
        """Begin a cue (``target`` good frames, default ``target_good_frames``).

        The head reference carries over between cues.
        """
        self._cue = str(cue)
        self._target = int(target) if target is not None else int(self.cfg.target_good_frames)
        self._state = GaugeState.SETTLING
        self._started_ms = int(t_ms)
        self._elapsed_ms = 0
        self._gaze = []
        self._ear = []
        self._rejected = Counter()
        self._last_reason = None

    @property
    def status(self) -> GaugeStatus:
        user_reasons = {k: v for k, v in self._rejected.items() if k not in _NEUTRAL_REASONS}
        dominant = max(user_reasons, key=user_reasons.get) if user_reasons else None
        return GaugeStatus(
            cue=self._cue,
            state=self._state.value,
            good=len(self._gaze),
            target=self._target,
            elapsed_ms=self._elapsed_ms,
            rejected=dict(self._rejected),
            dominant_reason=dominant,
            last_reason=self._last_reason,
        )

    def offer(
        self, obs: FrameObservation, gaze: Optional[GazeVector], t_ms: int
    ) -> Tuple[bool, Optional[str]]:
        """Judge one frame; ``(accepted, reject_reason)``."""
        if self._state not in (GaugeState.SETTLING, GaugeState.COLLECTING):
            return False, NOT_ACTIVE
        self._elapsed_ms = max(0, int(t_ms) - self._started_ms)
        if self._elapsed_ms >= int(self.cfg.cue_timeout_ms):
            self._finish_on_timeout()
            return False, NOT_ACTIVE

        reason = self._reject_reason(obs, gaze)
        self._last_reason = reason
        if reason is not None:
            self._rejected[reason] += 1
            return False, reason

        self._gaze.append((math.degrees(gaze.gaze_yaw), math.degrees(gaze.gaze_pitch)))
        self._ear.append(float(obs.quality.min_eye_openness))
        if len(self._gaze) >= self._target:
            self._state = GaugeState.DONE
        return True, None

    def extend(self, target: int) -> None:
        """Raise this cue's target and go on collecting (a done cue reopens).

        The frames already counted stay; the cue's timeout still runs from its
        start.
        """
        self._target = max(self._target, int(target))
        if self._state == GaugeState.DONE and len(self._gaze) < self._target:
            self._state = GaugeState.COLLECTING

    def tick(self, t_ms: int) -> GaugeStatus:
        """Advance the clock without a frame (no face analysed) and report."""
        if self._state in (GaugeState.SETTLING, GaugeState.COLLECTING):
            self._elapsed_ms = max(0, int(t_ms) - self._started_ms)
            if self._elapsed_ms >= int(self.cfg.cue_timeout_ms):
                self._finish_on_timeout()
        return self.status

    # -- internals --------------------------------------------------------
    def _finish_on_timeout(self) -> None:
        enough = len(self._gaze) >= int(self.cfg.min_samples_per_class)
        self._state = GaugeState.DONE if enough else GaugeState.TIMED_OUT

    def _reject_reason(self, obs: FrameObservation, gaze: Optional[GazeVector]) -> Optional[str]:
        if not obs.face_valid:
            return obs.invalid_reason or InvalidReason.NO_FACE.value
        if gaze is None or not (math.isfinite(gaze.gaze_yaw) and math.isfinite(gaze.gaze_pitch)):
            return InvalidReason.BACKBONE_FAILED.value
        if not (float(gaze.confidence) >= float(self.cfg.min_sample_confidence)):
            return LOW_GAZE_CONFIDENCE
        if self._elapsed_ms < int(self.cfg.cue_settle_ms):
            return SETTLING
        self._state = GaugeState.COLLECTING

        head = (math.degrees(obs.head_pose.yaw), math.degrees(obs.head_pose.pitch))
        if self.eye_based and all(map(math.isfinite, head)):
            if self._head_reference is None:
                self._head_reference = head
            deviation = max(
                abs(head[0] - self._head_reference[0]), abs(head[1] - self._head_reference[1])
            )
            if deviation > float(self.cfg.max_head_deviation_deg):
                return HEAD_MOVED

        reference = self._direction_reference
        if (not self.eye_based and reference is not None and bool(self.cfg.cue_direction_gate)
                and all(map(math.isfinite, head))):
            # Presenter-centric: yaw > 0 turns toward the image right, the presenter's left.
            reason = direction_reason(self._cue, -(head[0] - reference[0]), head[1] - reference[1], self.cfg)
            if reason is not None:
                return reason

        if self.eye_based and len(self._ear) >= _MIN_FRAMES_FOR_STATS:
            reference = float(np.median(self._ear))
            ear = float(obs.quality.min_eye_openness)
            if reference > 0.0 and ear < self.blink_ratio * reference:
                return BLINK

        if len(self._gaze) >= _MIN_FRAMES_FOR_STATS:
            values = np.asarray(self._gaze, dtype=np.float64)
            centre = np.median(values, axis=0)
            spread = _MAD_TO_SIGMA * np.median(np.abs(values - centre), axis=0)
            scale = np.maximum(spread, float(self.cfg.sigma_min_deg))
            offset = np.abs(np.asarray([math.degrees(gaze.gaze_yaw), math.degrees(gaze.gaze_pitch)]) - centre)
            if bool(np.any(offset > float(self.cfg.outlier_k) * scale)):
                return OUTLIER
        return None
