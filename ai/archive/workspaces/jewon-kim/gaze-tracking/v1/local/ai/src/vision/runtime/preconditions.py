"""Set-up check before calibration: is this the scene the model was built for?

Everything downstream assumes one presenter, centred, at a laptop distance,
facing a webcam at the top centre of the screen, in usable light.  When that is
clearly false the measurement does not fail loudly -- it is quietly wrong (the
wrong person's gaze, an iris a few pixels wide, a backlit face).  This check
runs on the live preview before the first calibration cue and answers PASS,
RETRY or REJECT, with the reason and an actionable hint.

Rules
-----
* PASS once every check has held for ``hold_ms`` without interruption.
* REJECT once a single check has failed for ``reject_after_ms`` without
  interruption -- the user is not merely still settling.
* RETRY otherwise (settling, or a failure that has not lasted yet).

A blink (EYES_CLOSED) or a frame whose crops failed says nothing about the set
up, so such frames neither advance nor break the hold.  A lost face resets the
hold and counts toward rejection as NO_FACE.

Whether a REJECT actually stops the take is the caller's decision
(``runtime.policy`` / ``preconditions.strict``): research tools record it
and carry on.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from vision.config import PreconditionConfig
from vision.preprocess.headpose import focal_length_px
from vision.schemas import FrameObservation, HeadPose, InvalidReason

#: Adult iris diameter in centimetres (anatomically very stable, ~11.7 mm).
IRIS_DIAMETER_CM = 1.17


class PreconditionStatus(str, Enum):
    PASS = "PASS"
    RETRY = "RETRY"
    REJECT = "REJECT"


class PreconditionReason(str, Enum):
    OK = "OK"
    NO_FACE = "NO_FACE"
    MULTIPLE_FACES = "MULTIPLE_FACES"
    OFF_CENTER = "OFF_CENTER"
    TOO_FAR = "TOO_FAR"
    TOO_CLOSE = "TOO_CLOSE"
    FACING_AWAY = "FACING_AWAY"
    TOO_DARK = "TOO_DARK"
    BACKLIT = "BACKLIT"
    LOW_FPS = "LOW_FPS"


#: Check order is also the report order and the tie-break for "which reason".
_CHECK_ORDER: Tuple[PreconditionReason, ...] = (
    PreconditionReason.NO_FACE,
    PreconditionReason.MULTIPLE_FACES,
    PreconditionReason.OFF_CENTER,
    PreconditionReason.TOO_FAR,
    PreconditionReason.TOO_CLOSE,
    PreconditionReason.FACING_AWAY,
    PreconditionReason.TOO_DARK,
    PreconditionReason.BACKLIT,
    PreconditionReason.LOW_FPS,
)

HINTS: Dict[str, str] = {
    PreconditionReason.OK.value: "Hold still for a moment.",
    PreconditionReason.NO_FACE.value: "Sit in front of the camera so your face is visible.",
    PreconditionReason.MULTIPLE_FACES.value: "Only one person should be in the picture.",
    PreconditionReason.OFF_CENTER.value: "Move so your face is in the middle of the picture.",
    PreconditionReason.TOO_FAR.value: "Move closer to the camera.",
    PreconditionReason.TOO_CLOSE.value: "Move back a little from the camera.",
    PreconditionReason.FACING_AWAY.value: "Face the screen directly.",
    PreconditionReason.TOO_DARK.value: "Your face is too dark. Turn on a light in front of you.",
    PreconditionReason.BACKLIT.value: "There is strong light behind you. Close the blind or face the light.",
    PreconditionReason.LOW_FPS.value: "The camera is too slow. Close other apps using the camera or CPU.",
}

#: Invalid frames that say nothing about the set-up.
_NEUTRAL_INVALID = frozenset(
    {
        InvalidReason.EYES_CLOSED.value,
        InvalidReason.LOW_FACE_CONFIDENCE.value,
        InvalidReason.CROP_FAILED.value,
        InvalidReason.BACKBONE_FAILED.value,
    }
)

#: Gaps used to estimate the analysed frame rate.
_FPS_WINDOW = 8


@dataclass
class CheckResult:
    name: str
    ok: bool
    value: Optional[float] = None
    limit: Optional[str] = None


@dataclass
class PreconditionReport:
    status: str
    reason: str
    hint: str
    #: True when ``status`` is REJECT and the config is strict.
    blocking: bool = False
    #: How long every check has held (0 while something fails).
    held_ms: int = 0
    #: How long the reported failing check has been failing.
    failing_ms: int = 0
    checks: List[CheckResult] = field(default_factory=list)
    #: The raw numbers behind the checks (distance_cm, iris_px, ...).
    measurements: Dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == PreconditionStatus.PASS.value

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def iris_distance_cm(iris_px: float, image_size: Tuple[int, int]) -> float:
    """Camera-to-eye distance from the iris size (pinhole model, 63 deg vFOV)."""
    if not (iris_px > 0.0) or image_size is None:
        return math.nan
    return focal_length_px(image_size) * IRIS_DIAMETER_CM / float(iris_px)


def head_distance_cm(head_pose: HeadPose, image_size: Optional[Tuple[int, int]]) -> float:
    """Camera-to-head distance from the face-mesh fit, cm; NaN when unknown.

    MediaPipe fits its whole face model to the landmarks, so this needs no eye
    and stays put when the head turns or the lids drop -- unlike the iris size,
    which shrinks or is guessed when the eyes are hidden.  ``depth_proxy`` is
    that distance over the focal length both head-pose estimators share.
    """
    depth = float(head_pose.depth_proxy)
    if not (math.isfinite(depth) and depth > 0.0) or image_size is None:
        return math.nan
    return depth * focal_length_px(image_size)


class PreconditionChecker:
    """Feed it preview observations; it reports PASS / RETRY / REJECT.

    ``eye_based`` adds the iris-size floor, which only an eye-reading backbone
    needs; the head-pose backbone is measurable as long as the face can be found.
    """

    def __init__(self, cfg: PreconditionConfig, *, eye_based: bool = False) -> None:
        self.cfg = cfg
        self.eye_based = bool(eye_based)
        self.reset()

    def reset(self) -> None:
        self._hold_since: Optional[int] = None
        self._second_since: Optional[int] = None
        self._failing_since: Dict[str, int] = {}
        self._times: Deque[int] = deque(maxlen=_FPS_WINDOW + 1)
        self._last: Optional[PreconditionReport] = None

    @property
    def last_report(self) -> Optional[PreconditionReport]:
        return self._last

    def update(self, obs: FrameObservation) -> PreconditionReport:
        t_ms = int(obs.t_ms)
        if self._times and t_ms < self._times[-1]:
            self._times.clear()  # a new take or a rewound video
        self._times.append(t_ms)

        if not obs.face_valid and obs.invalid_reason in _NEUTRAL_INVALID:
            # Says nothing about the set-up: keep every timer where it is.
            report = self._report(t_ms, self._last.checks if self._last else [], {})
            self._last = report
            return report

        checks, measurements = self._evaluate(obs)
        failing = [c.name for c in checks if not c.ok]
        if failing:
            self._hold_since = None
        elif self._hold_since is None:
            self._hold_since = t_ms
        for name in [n for n in self._failing_since if n not in failing]:
            del self._failing_since[name]
        for name in failing:
            self._failing_since.setdefault(name, t_ms)

        report = self._report(t_ms, checks, measurements)
        self._last = report
        return report

    # -- internals --------------------------------------------------------
    def _report(self, t_ms: int, checks: List[CheckResult], measurements: Dict[str, float]) -> PreconditionReport:
        cfg = self.cfg
        if self._failing_since:
            # The check failing the longest explains the state best; ties go to
            # the earlier check in the documented order.
            order = {r.value: i for i, r in enumerate(_CHECK_ORDER)}
            name = min(self._failing_since, key=lambda n: (self._failing_since[n], order.get(n, 99)))
            failing_ms = t_ms - self._failing_since[name]
            if failing_ms >= int(cfg.reject_after_ms):
                status = PreconditionStatus.REJECT
            else:
                status = PreconditionStatus.RETRY
            return PreconditionReport(
                status=status.value, reason=name, hint=HINTS[name],
                blocking=status is PreconditionStatus.REJECT and bool(cfg.strict),
                held_ms=0, failing_ms=int(failing_ms), checks=checks, measurements=measurements,
            )
        held = 0 if self._hold_since is None else t_ms - self._hold_since
        status = PreconditionStatus.PASS if self._hold_since is not None and held >= int(cfg.hold_ms) else PreconditionStatus.RETRY
        reason = PreconditionReason.OK.value
        return PreconditionReport(
            status=status.value, reason=reason, hint=HINTS[reason], blocking=False,
            held_ms=int(held), failing_ms=0, checks=checks, measurements=measurements,
        )

    def _analysed_fps(self) -> Optional[float]:
        if len(self._times) < 4:
            return None
        gaps = np.diff(np.asarray(self._times, dtype=np.float64))
        gaps = gaps[gaps > 0]
        if gaps.size == 0:
            return None
        return 1000.0 / float(np.median(gaps))

    def _evaluate(self, obs: FrameObservation) -> Tuple[List[CheckResult], Dict[str, float]]:
        cfg = self.cfg
        R = PreconditionReason
        checks: List[CheckResult] = []
        measurements: Dict[str, float] = {}

        fps = self._analysed_fps()
        if fps is not None:
            measurements["analysis_fps"] = round(fps, 2)

        scene = obs.scene
        has_face = obs.invalid_reason != InvalidReason.NO_FACE.value and obs.face_bbox is not None
        if not has_face:
            checks.append(CheckResult(R.NO_FACE.value, False))
            if fps is not None:
                checks.append(CheckResult(R.LOW_FPS.value, fps >= float(cfg.min_analysis_fps), fps,
                                          f">= {cfg.min_analysis_fps}"))
            return checks, measurements
        checks.append(CheckResult(R.NO_FACE.value, True))

        second = float(scene.second_face_area_ratio) if scene is not None else 0.0
        measurements["second_face_area_ratio"] = round(second, 3)
        # A second person only once seen for a while: one frame's false find is not one.
        if second > float(cfg.max_second_face_area_ratio):
            if self._second_since is None:
                self._second_since = int(obs.t_ms)
            crowded = int(obs.t_ms) - self._second_since >= int(cfg.second_face_confirm_ms)
        else:
            self._second_since = None
            crowded = False
        checks.append(CheckResult(R.MULTIPLE_FACES.value, not crowded,
                                  second, f"<= {cfg.max_second_face_area_ratio}"))

        width, height = obs.image_size or (0, 0)
        x, y, w, h = obs.face_bbox
        if width > 0 and height > 0:
            dx = abs((x + w / 2.0) / width - 0.5)
            dy = abs((y + h / 2.0) / height - 0.5)
            measurements["center_offset_x"] = round(dx, 3)
            measurements["center_offset_y"] = round(dy, 3)
            off = dx > float(cfg.max_center_offset_x) or dy > float(cfg.max_center_offset_y)
            off = off or obs.invalid_reason == InvalidReason.OUT_OF_FRAME.value
            checks.append(CheckResult(R.OFF_CENTER.value, not off, max(dx, dy),
                                      f"x <= {cfg.max_center_offset_x}, y <= {cfg.max_center_offset_y}"))
            face_height = h / float(height)
            face_area = (w * h) / float(width * height)
            measurements["face_height_ratio"] = round(face_height, 3)
            measurements["face_area_ratio"] = round(face_area, 4)
        else:
            face_height = face_area = math.nan

        # Distance only as information: the bounds below are about whether the
        # face can be found and tracked, not about an ideal distance.  The
        # face-mesh distance needs no eye; the iris size is the fallback.
        iris = float(scene.iris_diameter_px) if scene is not None else 0.0
        distance = head_distance_cm(obs.head_pose, obs.image_size)
        if not math.isfinite(distance):
            distance = iris_distance_cm(iris, obs.image_size)
        measurements["iris_px"] = round(iris, 2)
        if math.isfinite(distance):
            measurements["distance_cm"] = round(distance, 1)
        too_far = obs.invalid_reason == InvalidReason.FACE_TOO_SMALL.value or (
            math.isfinite(face_area) and face_area < float(cfg.min_face_area_ratio)
        )
        if self.eye_based:
            too_far = too_far or (iris > 0.0 and iris < float(cfg.min_iris_px))
        checks.append(CheckResult(
            R.TOO_FAR.value, not too_far, face_area if math.isfinite(face_area) else None,
            f"face area >= {cfg.min_face_area_ratio}" + (f", iris >= {cfg.min_iris_px}px" if self.eye_based else ""),
        ))
        too_close = math.isfinite(face_height) and face_height > float(cfg.max_face_height_ratio)
        checks.append(CheckResult(R.TOO_CLOSE.value, not too_close, face_height if math.isfinite(face_height) else None,
                                  f"face height <= {cfg.max_face_height_ratio} of frame"))

        yaw, pitch = math.degrees(obs.head_pose.yaw), math.degrees(obs.head_pose.pitch)
        measurements["head_yaw_deg"] = round(yaw, 1)
        measurements["head_pitch_deg"] = round(pitch, 1)
        facing = abs(yaw) <= float(cfg.max_head_yaw_deg) and abs(pitch) <= float(cfg.max_head_pitch_deg)
        checks.append(CheckResult(R.FACING_AWAY.value, facing, max(abs(yaw), abs(pitch)),
                                  f"yaw <= {cfg.max_head_yaw_deg}, pitch <= {cfg.max_head_pitch_deg}"))

        brightness = float(obs.quality.face_brightness)
        backlight = float(obs.quality.backlight_ratio)
        measurements["face_brightness"] = round(brightness, 1)
        measurements["backlight_ratio"] = round(backlight, 2)
        checks.append(CheckResult(R.TOO_DARK.value, brightness >= float(cfg.min_face_brightness), brightness,
                                  f">= {cfg.min_face_brightness}"))
        checks.append(CheckResult(R.BACKLIT.value, backlight <= float(cfg.max_backlight_ratio), backlight,
                                  f"<= {cfg.max_backlight_ratio}"))

        if fps is not None:
            checks.append(CheckResult(R.LOW_FPS.value, fps >= float(cfg.min_analysis_fps), fps,
                                      f">= {cfg.min_analysis_fps}"))
        return checks, measurements
