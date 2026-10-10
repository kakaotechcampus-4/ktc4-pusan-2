"""Live measurement conditions: how far the scene has drifted from calibration.

The calibration anchors are only as good as the conditions they were measured
in.  When the presenter later sits further back, moves off-centre, or the
landmarks get shaky, the gaze is still estimated -- just less trustworthily.  Stopping the take for that would punish normal behaviour,
so instead every live frame is compared with the calibration scene and a
``reliability`` in ``[0, 1]`` is reported in a separate ``SESSION_CONDITION``
event:

* each signal maps its deviation linearly from 1.0 (at its ``*_warn`` bound)
  to ``fail_reliability`` (at its ``*_fail`` bound);
* reliability is the **minimum** over the signals, so ``issues`` always names
  what pulled it down (an average would hide one bad signal behind good ones);
* a signal that cannot be measured on a frame (no iris size, no scene) counts
  as 1.0 -- absence of evidence is not a penalty.

The head signal only exists for eye-reading backbones (``head_is_gaze=False``):
there the head was held still during calibration and a turned head degrades
the eye-gaze estimate.  It is the distance to the *nearest* calibration
posture (one per cue).  With the head-pose backbone (``head_is_gaze=True``)
the head direction IS the gaze -- looking up or to the side is a direction
the classifier reports (OTHER with its direction), not a worse measurement --
so it never lowers reliability.

What counts
-----------
Reliability is "how likely is this judgement wrong or missing", so every
signal must have a path to a wrong or missing head angle:

* position / distance -- a move from the calibration seat changes the head
  angle needed for the same target (below);
* jitter -- the measured head angles scatter frame to frame (shaky
  landmarks), so a frame can land on the neighbouring target;
* size -- a face near the detector's floor (or filling the frame) is about to
  lose its landmarks;
* valid -- frames that could not be judged at all.

Causes are not signals: a light change or a second person in the picture does
not by itself change the measured angle.  When light makes the landmarks
shaky it shows as jitter (or lost frames); a second person who takes over the
measurement is FACE_REPLACED.  A distinct second face is still reported, as a
*notice* that leaves reliability alone.

Position drift
--------------
The anchors were measured from where the presenter sat during calibration.
Moving the head changes the head angle needed to look at the same target, so
a move is read as the head-angle error it causes:

* sideways or up/down by ``m`` cm at distance ``D``: ``atan(m / D)``;
* closer or further: the change of the farthest cue's angle from the screen
  centre, ``|atan(tan(span) x D0 / D1) - span|``.

Distances come from MediaPipe's face-mesh fit (``HeadPose.depth_proxy``): the
whole face model is fitted, so the distance needs no eye and does not change
when the head turns down or aside and the eyes disappear.  Only without it
(a frame or a calibration that lacks it) the iris size (11.7 mm) stands in.
A move in the picture is ``pixels x distance / focal``, with the pinhole focal
of ``preprocess.headpose``.
Turning the head moves the face in the picture by about
``head_radius_cm x sin(angle)`` without the head moving, so that part is taken
out first.  The error fails at the smallest separation between the calibrated
cue postures (a move that can carry a look all the way to the neighbouring
target, 6-12 deg) and warns at 60 % of that; beyond the fail bound for
``drift_confirm_ms`` (2 s) the measurement is unusable (MOVED_TOO_FAR).  It is
lenient on purpose: presenters shift in their seat, and only a move that
changes what the measurement means should stop it.  ``drift`` on
the state reports the move in cm and degrees for display.

Severe situations
-----------------
Three situations are *severe*: the main face has been gone for
``face_lost_ms`` (FACE_LOST), the face being measured is no longer the
calibrated one -- it jumped between two sightings (another detection, not a
move: the presenter moving away or aside, however far, changes the face
gradually) and did not come back to the calibrated place for
``replace_confirm_ms`` (FACE_REPLACED) -- or the presenter moved too far from
the calibration position (MOVED_TOO_FAR).

A second person counts only as a distinct face (not the main face found twice,
not smaller than the detector floor; see ``preprocess.pipeline``) seen for
``second_face_confirm_ms``.  Reliability is then 0 and the
runtime forces gaze decisions to UNCERTAIN rather than report a gaze it cannot
read.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional, Sequence, Tuple

import numpy as np

from gaze_lab.config import ConditionConfig
from gaze_lab.preprocess.headpose import focal_length_px
from gaze_lab.runtime.preconditions import IRIS_DIAMETER_CM, head_distance_cm
from gaze_lab.schemas import FrameObservation, InvalidReason, SessionConditionEvent


class ConditionIssue(str, Enum):
    HEAD_TURNED = "HEAD_TURNED"
    TOO_FAR = "TOO_FAR"
    TOO_CLOSE = "TOO_CLOSE"
    OFF_CENTER = "OFF_CENTER"
    SECOND_FACE = "SECOND_FACE"
    LOW_VALID_RATIO = "LOW_VALID_RATIO"
    NOISY_TRACKING = "NOISY_TRACKING"
    FACE_LOST = "FACE_LOST"
    FACE_REPLACED = "FACE_REPLACED"
    MOVED_TOO_FAR = "MOVED_TOO_FAR"


SEVERE_ISSUES = frozenset({
    ConditionIssue.FACE_LOST.value, ConditionIssue.FACE_REPLACED.value, ConditionIssue.MOVED_TOO_FAR.value,
})

#: Cue order for the drift limits (deterministic across ports).
_CUES = ("CAMERA", "SCREEN", "BOTTOM")


@dataclass(frozen=True)
class SceneBaseline:
    """The calibration scene, as medians over the accepted calibration frames."""

    #: Normalised face-centre ``(x, y)``.
    centre: Tuple[float, float]
    #: Face bbox area as a fraction of the frame.
    face_area: float
    #: Iris diameter in pixels (0 when unknown).
    iris_px: float
    #: Mean face luminance 0-255.
    brightness: float
    #: Head ``(yaw_deg, pitch_deg)``, median over all calibration frames.
    head: Tuple[float, float]
    #: Median head ``(yaw_deg, pitch_deg)`` per calibration cue.  Empty when the
    #: frames carried no cue (a re-anchor), in which case ``head`` is used.
    head_poses: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    #: Camera-to-head distance from the face-mesh fit, cm (0 when unknown).
    depth_cm: float = 0.0

    def head_deviation(self, yaw_deg: float, pitch_deg: float) -> float:
        """Degrees to the nearest calibrated head posture (max over the two axes)."""
        poses = list(self.head_poses.values()) or [self.head]
        return min(max(abs(yaw_deg - y), abs(pitch_deg - p)) for y, p in poses)

    def shifted_head_poses(self, new_camera_head: Tuple[float, float]) -> Dict[str, Tuple[float, float]]:
        """Every cue posture moved so the CAMERA one lands on ``new_camera_head``."""
        camera = self.head_poses.get("CAMERA", self.head)
        dy, dp = new_camera_head[0] - camera[0], new_camera_head[1] - camera[1]
        return {cue: (y + dy, p + dp) for cue, (y, p) in self.head_poses.items()}


def _centre(obs: FrameObservation) -> Optional[Tuple[float, float]]:
    if obs.face_bbox is None or obs.image_size is None:
        return None
    x, y, w, h = obs.face_bbox
    width, height = obs.image_size
    if w <= 0 or h <= 0 or width <= 0 or height <= 0:
        return None
    return ((x + w / 2.0) / width, (y + h / 2.0) / height)


def _face_area(obs: FrameObservation) -> Optional[float]:
    if obs.face_bbox is None or obs.image_size is None:
        return None
    _, _, w, h = obs.face_bbox
    width, height = obs.image_size
    if w <= 0 or h <= 0 or width <= 0 or height <= 0:
        return None
    return (w * h) / float(width * height)


class SceneBaselineAccumulator:
    """Collects the scene of accepted calibration frames."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._rows: List[Tuple[float, float, float, float, float, float, float, float]] = []
        self._cues: List[Optional[str]] = []

    def add(self, obs: FrameObservation, cue: Optional[str] = None) -> None:
        centre, area = _centre(obs), _face_area(obs)
        if centre is None or area is None:
            return
        iris = float(obs.scene.iris_diameter_px) if obs.scene is not None else 0.0
        self._rows.append(
            (
                centre[0], centre[1], area, iris, float(obs.quality.face_brightness),
                math.degrees(obs.head_pose.yaw), math.degrees(obs.head_pose.pitch),
                head_distance_cm(obs.head_pose, obs.image_size),
            )
        )
        self._cues.append(cue)

    def __len__(self) -> int:
        return len(self._rows)

    def build(self) -> Optional[SceneBaseline]:
        if not self._rows:
            return None
        rows = np.asarray(self._rows, dtype=np.float64)
        med = np.nanmedian(rows[:, :7], axis=0)
        iris = rows[:, 3]
        iris = iris[iris > 0]
        depth = rows[:, 7]
        depth = depth[np.isfinite(depth) & (depth > 0)]
        head_poses: Dict[str, Tuple[float, float]] = {}
        for cue in sorted({c for c in self._cues if c is not None}):
            heads = rows[[c == cue for c in self._cues]][:, 5:7]
            heads = heads[np.all(np.isfinite(heads), axis=1)]
            if heads.shape[0]:
                cue_med = np.median(heads, axis=0)
                head_poses[cue] = (float(cue_med[0]), float(cue_med[1]))
        return SceneBaseline(
            centre=(float(med[0]), float(med[1])),
            face_area=float(med[2]),
            iris_px=float(np.median(iris)) if iris.size else 0.0,
            brightness=float(med[4]),
            head=(float(med[5]), float(med[6])),
            head_poses=head_poses,
            depth_cm=float(np.median(depth)) if depth.size else 0.0,
        )


@dataclass(frozen=True)
class Drift:
    """How far the head moved from where it was calibrated, and what that costs."""

    #: The move, presenter-centric: their own right and up; ``closer_cm`` > 0 is closer.
    right_cm: float
    up_cm: float
    closer_cm: float
    #: Camera-to-face distance now and at calibration, cm.
    distance_cm: float
    calibrated_distance_cm: float
    #: Head-angle error from the move sideways/up-down and from the distance change.
    position_deg: float
    distance_deg: float
    warn_deg: float
    fail_deg: float

    @property
    def deg(self) -> float:
        return max(self.position_deg, self.distance_deg)

    def to_dict(self) -> Dict[str, float]:
        return {
            "right_cm": self.right_cm, "up_cm": self.up_cm, "closer_cm": self.closer_cm,
            "distance_cm": self.distance_cm, "calibrated_distance_cm": self.calibrated_distance_cm,
            "position_deg": self.position_deg, "distance_deg": self.distance_deg, "deg": self.deg,
            "warn_deg": self.warn_deg, "fail_deg": self.fail_deg,
        }


def _min_cue_separation(base: SceneBaseline) -> Optional[float]:
    """Smallest distance between two calibrated cue postures, deg (None with fewer than two)."""
    poses = [base.head_poses[c] for c in _CUES if c in base.head_poses]
    seps = [math.hypot(a[0] - b[0], a[1] - b[1]) for i, a in enumerate(poses) for b in poses[i + 1:]]
    return min(seps) if seps else None


def drift_limits(base: SceneBaseline, cfg: ConditionConfig) -> Tuple[float, float, float]:
    """``(warn_deg, fail_deg, span_deg)`` for this calibration.

    The fail bound is ``drift_fail_share`` x the smallest separation between the
    calibrated cue postures, clamped; ``span`` is the farthest cue from the
    screen centre (half the lens-script separation without a screen cue).
    """
    poses = [(c, base.head_poses[c]) for c in _CUES if c in base.head_poses]
    sep = _min_cue_separation(base)
    lo, hi = float(cfg.drift_fail_min_deg), float(cfg.drift_fail_max_deg)
    fail = min(hi, max(lo, float(cfg.drift_fail_share) * sep)) if sep is not None else lo
    screen = base.head_poses.get("SCREEN")
    others = [p for c, p in poses if c != "SCREEN"]
    if screen is not None and others:
        span = max(math.hypot(p[0] - screen[0], p[1] - screen[1]) for p in others)
    elif "CAMERA" in base.head_poses and "BOTTOM" in base.head_poses:
        cam, bot = base.head_poses["CAMERA"], base.head_poses["BOTTOM"]
        span = math.hypot(cam[0] - bot[0], cam[1] - bot[1]) / 2.0
    else:
        span = float(cfg.drift_default_span_deg)
    return float(cfg.drift_warn_share) * fail, fail, span


def jitter_limits(base: SceneBaseline, cfg: ConditionConfig) -> Tuple[float, float]:
    """``(warn_deg, fail_deg)`` for the head-direction noise of this calibration."""
    sep = _min_cue_separation(base)
    lo, hi = float(cfg.jitter_fail_min_deg), float(cfg.jitter_fail_max_deg)
    fail = min(hi, max(lo, float(cfg.jitter_fail_share) * sep)) if sep is not None else lo
    return float(cfg.jitter_warn_share) * fail, fail


def head_jitter_deg(samples: Sequence[Tuple[int, float, float]], max_gap_ms: int, min_samples: int) -> Optional[float]:
    """Frame-to-frame noise of the head angles, deg; None with too few frames.

    ``samples`` are ``(t_ms, yaw_deg, pitch_deg)`` in time order.  The second
    difference ``x[i] - 2 x[i-1] + x[i-2]`` cancels a steady turn and keeps the
    frame-to-frame scatter; for white noise of sd ``s`` its sd is ``s x sqrt(6)``,
    so ``s = 1.4826 x median|d2| / sqrt(6)`` per axis (the median ignores the
    few frames where a turn starts or stops).  The larger axis is returned.
    Triples across a gap longer than ``max_gap_ms`` are skipped.
    """
    d2: List[Tuple[float, float]] = []
    for i in range(2, len(samples)):
        (t0, y0, p0), (t1, y1, p1), (t2, y2, p2) = samples[i - 2], samples[i - 1], samples[i]
        if t1 - t0 > max_gap_ms or t2 - t1 > max_gap_ms:
            continue
        d2.append((y2 - 2.0 * y1 + y0, p2 - 2.0 * p1 + p0))
    if len(d2) < int(min_samples):
        return None
    arr = np.abs(np.asarray(d2, dtype=np.float64))
    scale = 1.4826 / math.sqrt(6.0)
    return float(max(np.median(arr[:, 0]), np.median(arr[:, 1])) * scale)


def _distances(obs: FrameObservation, base: SceneBaseline) -> Optional[Tuple[float, float]]:
    """``(now, at calibration)`` camera distance in cm, both from one source; None if unknown.

    The face-mesh distance when this frame and the calibration both have it,
    else the iris size -- never one of each, which would read the difference
    between the two estimates as a move.
    """
    now = head_distance_cm(obs.head_pose, obs.image_size)
    if math.isfinite(now) and base.depth_cm > 0.0:
        return now, float(base.depth_cm)
    iris = float(obs.scene.iris_diameter_px) if obs.scene is not None else 0.0
    if iris > 0.0 and base.iris_px > 0.0:
        focal = focal_length_px(obs.image_size)
        return focal * IRIS_DIAMETER_CM / iris, focal * IRIS_DIAMETER_CM / base.iris_px
    return None


def measure_drift(
    obs: FrameObservation, base: SceneBaseline, cfg: ConditionConfig,
    limits: Optional[Tuple[float, float, float]] = None,
) -> Optional[Drift]:
    """The head's move from the calibration position (None without a distance or a face)."""
    centre = _centre(obs)
    distances = _distances(obs, base) if centre is not None else None
    if centre is None or distances is None:
        return None
    warn, fail, span = limits if limits is not None else drift_limits(base, cfg)
    width, height = obs.image_size
    d_now, d_cal = distances
    cm_per_px = d_now / focal_length_px(obs.image_size)
    dx = (centre[0] - base.centre[0]) * width * cm_per_px   # image right +
    dy = (centre[1] - base.centre[1]) * height * cm_per_px  # image down +
    yaw, pitch = obs.head_pose.yaw, obs.head_pose.pitch
    yaw0, pitch0 = math.radians(base.head[0]), math.radians(base.head[1])
    if all(map(math.isfinite, (yaw, pitch, yaw0, pitch0))):
        # A turned head moves the face in the picture (toward image right for
        # yaw > 0, up for pitch > 0) while the head itself stays put.
        radius = float(cfg.head_radius_cm)
        dx -= radius * (math.sin(yaw) * math.cos(pitch) - math.sin(yaw0) * math.cos(pitch0))
        dy += radius * (math.sin(pitch) - math.sin(pitch0))
    position = math.degrees(math.atan2(math.hypot(dx, dy), d_now))
    span_rad = math.radians(span)
    distance = abs(math.degrees(math.atan(math.tan(span_rad) * d_cal / d_now)) - span)
    return Drift(
        right_cm=-dx, up_cm=-dy, closer_cm=d_cal - d_now, distance_cm=d_now, calibrated_distance_cm=d_cal,
        position_deg=position, distance_deg=distance, warn_deg=warn, fail_deg=fail,
    )


@dataclass
class ConditionState:
    t_ms: int
    reliability: float
    issues: List[str] = field(default_factory=list)
    severe: bool = False
    components: Dict[str, float] = field(default_factory=dict)
    #: How far the head moved from the calibration position (None when unknown).
    drift: Optional[Dict[str, float]] = None
    #: Worth knowing but not lowering reliability (a distinct second face).
    notices: List[str] = field(default_factory=list)
    #: Head-direction noise over the recent window, deg (None when unknown).
    jitter_deg: Optional[float] = None

    def to_event(self) -> SessionConditionEvent:
        return SessionConditionEvent(
            t_ms=self.t_ms, reliability=self.reliability, issues=list(self.issues),
            severe=self.severe, components=dict(self.components),
        )


def _ramp(value: float, warn: float, fail: float, floor: float) -> float:
    """1.0 at or below ``warn``, ``floor`` at or above ``fail``, linear between."""
    if not math.isfinite(value) or value <= warn:
        return 1.0
    if value >= fail:
        return floor
    return 1.0 - (value - warn) / (fail - warn) * (1.0 - floor)


class ConditionMonitor:
    """Compares live frames with the calibration scene."""

    def __init__(self, cfg: ConditionConfig, baseline: SceneBaseline, *, head_is_gaze: bool = False) -> None:
        self.cfg = cfg
        self._baseline = baseline
        #: Head-pose backbone: a turned head is a gaze direction, not a condition.
        self.head_is_gaze = bool(head_is_gaze)
        self._limits = drift_limits(baseline, cfg)
        self._jitter_limits = jitter_limits(baseline, cfg)
        self._heads: Deque[Tuple[int, float, float]] = deque()
        self._valid: Deque[Tuple[int, bool]] = deque()
        self._last_face_ms: Optional[int] = None
        self._replaced_since: Optional[int] = None
        self._drifted_since: Optional[int] = None
        self._second_since: Optional[int] = None
        #: ``(t_ms, centre, area)`` of the last frame with a face.
        self._prev_face: Optional[Tuple[int, Tuple[float, float], float]] = None
        self._last: Optional[ConditionState] = None
        self._last_emitted: Optional[ConditionState] = None

    @property
    def baseline(self) -> SceneBaseline:
        return self._baseline

    @property
    def last(self) -> Optional[ConditionState]:
        return self._last

    def rebaseline(self, baseline: SceneBaseline) -> None:
        """Adopt a new reference scene (after a re-anchor) and forget the drift."""
        self._baseline = baseline
        self._limits = drift_limits(baseline, self.cfg)
        self._jitter_limits = jitter_limits(baseline, self.cfg)
        self._heads.clear()
        self._replaced_since = None
        self._drifted_since = None
        self._second_since = None
        self._prev_face = None
        self._last_emitted = None

    def update(self, obs: FrameObservation) -> ConditionState:
        cfg, base = self.cfg, self._baseline
        t_ms = int(obs.t_ms)
        floor = float(cfg.fail_reliability)
        components: Dict[str, float] = {}
        issues: List[str] = []

        # Valid-frame ratio over a sliding time window.
        self._valid.append((t_ms, bool(obs.face_valid)))
        while self._valid and (t_ms - self._valid[0][0] > int(cfg.window_ms) or self._valid[0][0] > t_ms):
            self._valid.popleft()
        ratio = sum(v for _, v in self._valid) / float(len(self._valid))
        shortfall = 1.0 - ratio
        components["valid"] = _ramp(shortfall, 1.0 - float(cfg.valid_warn_ratio),
                                    1.0 - float(cfg.valid_fail_ratio), floor)
        if ratio < float(cfg.valid_warn_ratio):
            issues.append(ConditionIssue.LOW_VALID_RATIO.value)

        centre = _centre(obs)
        has_face = centre is not None and obs.invalid_reason != InvalidReason.NO_FACE.value
        if self._last_face_ms is None or t_ms < self._last_face_ms:
            # The first frame (or a rewound stream) starts the loss clock.
            self._last_face_ms = t_ms
        if has_face:
            self._last_face_ms = t_ms
        severe: List[str] = []
        if not has_face and t_ms - self._last_face_ms >= int(cfg.face_lost_ms):
            severe.append(ConditionIssue.FACE_LOST.value)

        if has_face and not self.head_is_gaze:
            yaw, pitch = math.degrees(obs.head_pose.yaw), math.degrees(obs.head_pose.pitch)
            head_dev = base.head_deviation(yaw, pitch)
            components["head"] = _ramp(head_dev, float(cfg.head_warn_deg), float(cfg.head_fail_deg), floor)
            if head_dev > float(cfg.head_warn_deg):
                issues.append(ConditionIssue.HEAD_TURNED.value)

        # Head-direction noise over the recent window.
        yaw_deg, pitch_deg = math.degrees(obs.head_pose.yaw), math.degrees(obs.head_pose.pitch)
        if self._heads and t_ms < self._heads[-1][0]:
            self._heads.clear()  # a rewound stream
        if obs.face_valid and math.isfinite(yaw_deg) and math.isfinite(pitch_deg) \
                and math.isfinite(float(obs.head_pose.reprojection_error)):
            self._heads.append((t_ms, yaw_deg, pitch_deg))
        while self._heads and t_ms - self._heads[0][0] > int(cfg.jitter_window_ms):
            self._heads.popleft()
        jitter = head_jitter_deg(list(self._heads), int(cfg.jitter_max_gap_ms), int(cfg.jitter_min_samples))
        if jitter is not None:
            warn, fail = self._jitter_limits
            components["jitter"] = _ramp(jitter, warn, fail, floor)
            if jitter > warn:
                issues.append(ConditionIssue.NOISY_TRACKING.value)

        notices: List[str] = []
        drift: Optional[Drift] = None
        if has_face:
            drift = measure_drift(obs, base, cfg, self._limits)
            if drift is not None:
                warn, fail = drift.warn_deg, drift.fail_deg
                components["position"] = _ramp(drift.position_deg, warn, fail, floor)
                components["distance"] = _ramp(drift.distance_deg, warn, fail, floor)
                if drift.position_deg > warn:
                    issues.append(ConditionIssue.OFF_CENTER.value)
                if drift.distance_deg > warn:
                    issues.append((ConditionIssue.TOO_CLOSE if drift.closer_cm > 0 else ConditionIssue.TOO_FAR).value)
                if drift.deg >= fail:
                    if self._drifted_since is None:
                        self._drifted_since = t_ms
                    if t_ms - self._drifted_since >= int(cfg.drift_confirm_ms):
                        severe.append(ConditionIssue.MOVED_TOO_FAR.value)
                else:
                    self._drifted_since = None

            shift = max(abs(centre[0] - base.centre[0]), abs(centre[1] - base.centre[1]))

            # The face against the recognition floor: too small to find, or so big it leaves the frame.
            area = _face_area(obs)
            if area is not None:
                height_ratio = float(obs.face_bbox[3]) / float(obs.image_size[1])
                small = _ramp(float(cfg.small_face_warn_area) - area, 0.0,
                              float(cfg.small_face_warn_area) - float(cfg.small_face_fail_area), floor)
                large = _ramp(height_ratio, float(cfg.large_face_warn_height), float(cfg.large_face_fail_height), floor)
                components["size"] = min(small, large)
                if area < float(cfg.small_face_warn_area) and ConditionIssue.TOO_FAR.value not in issues:
                    issues.append(ConditionIssue.TOO_FAR.value)
                if height_ratio > float(cfg.large_face_warn_height) and ConditionIssue.TOO_CLOSE.value not in issues:
                    issues.append(ConditionIssue.TOO_CLOSE.value)

            second = float(obs.scene.second_face_area_ratio) if obs.scene is not None else 0.0
            if second >= float(cfg.second_face_area_ratio):
                if self._second_since is None:
                    self._second_since = t_ms
                if t_ms - self._second_since >= int(cfg.second_face_confirm_ms):
                    notices.append(ConditionIssue.SECOND_FACE.value)
            else:
                self._second_since = None

            area = _face_area(obs) or 0.0
            ratio_limit = float(cfg.replace_area_ratio)

            def changed(offset: float, ratio: float) -> bool:
                return offset > float(cfg.replace_center_offset) or not (1.0 / ratio_limit <= ratio <= ratio_limit)

            # The first sighting after calibration (or a re-anchor) is compared
            # with the calibrated face itself.
            prev = self._prev_face if self._prev_face is not None else (t_ms, base.centre, base.face_area)
            if t_ms - prev[0] <= int(cfg.face_lost_ms):
                step = max(abs(centre[0] - prev[1][0]), abs(centre[1] - prev[1][1]))
                if changed(step, area / prev[2] if prev[2] > 0 else 1.0):
                    self._replaced_since = t_ms  # another detection took over
            if self._replaced_since is not None:
                if not changed(shift, area / base.face_area if base.face_area > 0 else 1.0):
                    self._replaced_since = None  # back where the calibrated face was
                elif t_ms - self._replaced_since >= int(cfg.replace_confirm_ms):
                    severe.append(ConditionIssue.FACE_REPLACED.value)
            self._prev_face = (t_ms, centre, area)

        reliability = 0.0 if severe else min(components.values()) if components else 1.0
        state = ConditionState(
            t_ms=t_ms,
            reliability=float(reliability),
            issues=severe + [i for i in issues if i not in severe],
            severe=bool(severe),
            components=components,
            drift=drift.to_dict() if drift is not None else None,
            notices=notices,
            jitter_deg=jitter,
        )
        self._last = state
        return state

    def should_emit(self, state: ConditionState) -> bool:
        """First state, any change of issues or severity, a reliability move of
        at least ``emit_delta``, or the heartbeat.  Records what it released."""
        prev = self._last_emitted
        due = (
            prev is None
            or state.severe != prev.severe
            or set(state.issues) != set(prev.issues)
            or abs(state.reliability - prev.reliability) >= float(self.cfg.emit_delta)
            or state.t_ms - prev.t_ms >= int(self.cfg.heartbeat_ms)
            or state.t_ms < prev.t_ms
        )
        if due:
            self._last_emitted = state
        return due


def baseline_from_observations(observations: Sequence[FrameObservation]) -> Optional[SceneBaseline]:
    acc = SceneBaselineAccumulator()
    for obs in observations:
        acc.add(obs)
    return acc.build()
