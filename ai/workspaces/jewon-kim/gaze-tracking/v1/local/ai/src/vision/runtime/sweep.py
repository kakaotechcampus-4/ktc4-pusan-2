"""Head circle check: turn the head slowly until the ring around the face is full.

Runs between the set-up check and the calibration cues.  The presenter looks
straight ahead for a moment -- that pose is the centre of the circle -- then
turns the head slowly around in a circle.  A ring of ticks around the face
lights up in each direction the head reached while the face stayed tracked.

What it checks
--------------
* The face stays tracked in every direction a presenter's head goes during a
  talk.  Glasses glare, a dim side of the face or a face near the frame edge
  show up as directions that never fill (``missing``) or that lose the face
  (``lost``) -- before the take, not as unmeasured seconds in it.
* The direction reading is the presenter's own: turning to your right fills the
  RIGHT ticks, the convention of ``GAZE_DIRECTIONS`` and of the OTHER direction.

It never blocks on its own; like the set-up check, what to do with the result
is the caller's decision.  Its centre -- the presenter looking at the screen,
measured over ``neutral_frames`` (1 s) with its noise ``neutral_sigma_deg`` --
is the baseline the calibration starts from: the screen-centre look confirms
it (``CalibrationConfig.cue_confirm_*``) and the lens and script looks are
checked against it.

Geometry
--------
::

    offset = (right, up) = (-(yaw - yaw0), pitch - pitch0)      degrees from the centre pose
    reach  = hypot(right / reach_yaw_deg, up / reach_pitch_deg)  1.0 = far enough
    angle  = atan2(up / reach_pitch_deg, right / reach_yaw_deg)  0 = presenter's right, CCW

Yaw > 0 turns toward the image right, which is the presenter's LEFT, hence the
sign.  A tick lights when ``reach >= 1``; tick 0 is centred on RIGHT and the
indices run counter-clockwise, so with 32 ticks each of the 8 directions owns
4 of them.  At 8 FPS a smooth circle moves about one tick per frame, so two
turned frames in a row also fill the ticks between them (``max_gap_ms``,
``max_fill_arc_deg``).  A frame that moved faster than ``max_speed_deg_s``
lights nothing and asks for a slower turn.

Every tick also keeps how far the head has turned toward it (``tick_reach``,
0..1, 1 = lit), and each of the 8 directions reports the mean over its ticks
(``direction_progress``): a gauge per direction that fills as the head turns
further that way, before any tick lights.

Progress is never lost: frames without a face only pause the ring.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from vision.config import SweepConfig
from vision.schemas import GAZE_DIRECTIONS, FrameObservation

IDLE = "IDLE"
CENTERING = "CENTERING"
SWEEPING = "SWEEPING"
DONE = "DONE"
TIMED_OUT = "TIMED_OUT"

#: Turned faster than ``max_speed_deg_s`` between two frames.
TOO_FAST = "TOO_FAST"
#: The head pose could not be measured on a frame that had a face.
NO_HEAD_POSE = "NO_HEAD_POSE"
#: A frame with no usable face, when the observation names no reason.
NO_FACE = "NO_FACE"

#: Below this reach the head is "at the centre": no pointer, and losing the
#: face there is not blamed on a direction.
_POINTER_MIN_REACH = 0.35


def direction_of_angle(angle_deg: float) -> str:
    """The 45-degree sector of an angle (0 = presenter's right, counter-clockwise)."""
    return GAZE_DIRECTIONS[int(math.floor((angle_deg + 22.5) / 45.0)) % 8]


@dataclass(frozen=True)
class SweepStatus:
    """Where the ring stands after a frame."""

    state: str
    ticks: Tuple[bool, ...]
    elapsed_ms: int = 0
    #: Centre pose ``(yaw, pitch)`` in degrees, once measured.
    neutral_deg: Optional[Tuple[float, float]] = None
    #: Noise of that centre ``(yaw, pitch)``: 1.4826 x MAD of its frames, degrees.
    neutral_sigma_deg: Optional[Tuple[float, float]] = None
    #: This frame's head ``(right, up)`` in degrees from the centre (None without a pose).
    offset_deg: Optional[Tuple[float, float]] = None
    #: This frame's turn on the reach ellipse (1.0 = far enough to light a tick).
    reach: float = 0.0
    #: Angle the head points at (0 = presenter's right, CCW), when it is off the centre.
    pointer_deg: Optional[float] = None
    #: Why this frame lit nothing (NO_FACE, TOO_FAST, ...), else None.
    last_reason: Optional[str] = None
    #: Direction of the largest gap still to fill, after ``hint_after_ms`` without progress.
    hint: Optional[str] = None
    #: Directions that still have unlit ticks, in ``GAZE_DIRECTIONS`` order.
    missing: Tuple[str, ...] = ()
    #: Times the face was lost while turned toward each direction.
    lost: Dict[str, int] = field(default_factory=dict)
    #: Largest head turn (degrees from the centre) reached in each direction with the face tracked.
    reached_deg: Dict[str, float] = field(default_factory=dict)
    #: How far the head has turned toward each tick, 0..1 (1 = lit).
    tick_reach: Tuple[float, ...] = ()
    #: Per direction, the mean ``tick_reach`` of its ticks: a gauge per direction.
    direction_progress: Dict[str, float] = field(default_factory=dict)

    @property
    def filled(self) -> int:
        return sum(1 for t in self.ticks if t)

    @property
    def total(self) -> int:
        return len(self.ticks)

    @property
    def progress(self) -> float:
        return self.filled / self.total if self.ticks else 0.0

    @property
    def finished(self) -> bool:
        return self.state in (DONE, TIMED_OUT)

    def to_dict(self) -> Dict[str, object]:
        return {
            "state": self.state,
            "ticks": list(self.ticks),
            "filled": self.filled,
            "total": self.total,
            "progress": self.progress,
            "elapsed_ms": self.elapsed_ms,
            "neutral_deg": list(self.neutral_deg) if self.neutral_deg is not None else None,
            "neutral_sigma_deg": list(self.neutral_sigma_deg) if self.neutral_sigma_deg is not None else None,
            "offset_deg": list(self.offset_deg) if self.offset_deg is not None else None,
            "reach": self.reach,
            "pointer_deg": self.pointer_deg,
            "last_reason": self.last_reason,
            "hint": self.hint,
            "missing": list(self.missing),
            "lost": dict(self.lost),
            "reached_deg": dict(self.reached_deg),
            "tick_reach": list(self.tick_reach),
            "direction_progress": dict(self.direction_progress),
            "finished": self.finished,
        }


@dataclass
class _Turn:
    """The previous frame with a face, as the next one needs it."""

    t_ms: int
    offset: Tuple[float, float]
    angle: Optional[float]
    reach: float
    #: Not a jerk: the next frame may fill the ticks in between.
    bridge: bool


class HeadSweep:
    """The ring: ``start(t_ms)``, then ``offer(observation, t_ms)`` per analysed frame."""

    def __init__(self, cfg: SweepConfig) -> None:
        if int(cfg.ticks) <= 0 or int(cfg.ticks) % 8:
            raise ValueError(f"sweep.ticks must be a positive multiple of 8, got {cfg.ticks}")
        self.cfg = cfg
        self._n = int(cfg.ticks)
        self._tick_deg = 360.0 / self._n
        self._reset(IDLE, 0)

    def _reset(self, state: str, t_ms: int) -> None:
        self._state = state
        self._started = int(t_ms)
        self._ticks: List[bool] = [False] * self._n
        self._reach: List[float] = [0.0] * self._n
        self._centre: List[Tuple[float, float]] = []
        self._neutral: Optional[Tuple[float, float]] = None
        self._neutral_sigma: Optional[Tuple[float, float]] = None
        self._prev: Optional[_Turn] = None
        self._last_fill = int(t_ms)
        self._lost: Dict[str, int] = {}
        self._reached: Dict[str, float] = {}
        self._status = self._snapshot(int(t_ms), None, 0.0, None, None)

    # -- public ------------------------------------------------------------
    def start(self, t_ms: int) -> SweepStatus:
        self._reset(CENTERING, t_ms)
        return self._status

    @property
    def status(self) -> SweepStatus:
        return self._status

    def tick_direction(self, index: int) -> str:
        return direction_of_angle(index * self._tick_deg)

    def offer(self, obs: FrameObservation, t_ms: int) -> SweepStatus:
        if self._state not in (CENTERING, SWEEPING):
            return self._status
        t = int(t_ms)
        pose = _head_deg(obs)
        if pose is None:
            reason = NO_HEAD_POSE if obs.face_valid else (_reason(obs) or NO_FACE)
            self._lose_face()
            return self._finish(t, None, 0.0, None, reason)

        if self._state == CENTERING:
            self._centre.append(pose)
            if len(self._centre) < int(self.cfg.neutral_frames):
                return self._finish(t, None, 0.0, None, None)
            arr = np.asarray(self._centre, dtype=float)
            med = np.median(arr, axis=0)
            mad = np.median(np.abs(arr - med), axis=0)
            self._neutral = (float(med[0]), float(med[1]))
            self._neutral_sigma = (float(1.4826 * mad[0]), float(1.4826 * mad[1]))
            self._state = SWEEPING
            self._last_fill = t

        assert self._neutral is not None
        offset = (-(pose[0] - self._neutral[0]), pose[1] - self._neutral[1])
        nx = offset[0] / float(self.cfg.reach_yaw_deg)
        ny = offset[1] / float(self.cfg.reach_pitch_deg)
        reach = math.hypot(nx, ny)
        angle = math.degrees(math.atan2(ny, nx)) % 360.0 if reach >= _POINTER_MIN_REACH else None

        prev = self._prev
        if prev is not None and t > prev.t_ms:
            speed = math.hypot(offset[0] - prev.offset[0], offset[1] - prev.offset[1]) * 1000.0 / (t - prev.t_ms)
            if speed > float(self.cfg.max_speed_deg_s):
                self._prev = _Turn(t, offset, angle, reach, bridge=False)
                return self._finish(t, offset, reach, angle, TOO_FAST)

        lit = False
        if angle is not None:
            self._raise(self._tick_of(angle), reach)
            if reach >= 1.0:
                direction = direction_of_angle(angle)
                magnitude = math.hypot(*offset)
                if magnitude > self._reached.get(direction, 0.0):
                    self._reached[direction] = magnitude
                lit = self._light(self._tick_of(angle))
            if (prev is not None and prev.bridge and prev.angle is not None
                    and 0 < t - prev.t_ms <= int(self.cfg.max_gap_ms)):
                arc = (angle - prev.angle + 180.0) % 360.0 - 180.0
                if abs(arc) <= float(self.cfg.max_fill_arc_deg):
                    lit = self._fill_between(self._tick_of(prev.angle), self._tick_of(angle), arc,
                                             min(prev.reach, reach)) or lit
        if lit:
            self._last_fill = t
        self._prev = _Turn(t, offset, angle, reach, bridge=True)
        return self._finish(t, offset, reach, angle, None)

    # -- internals ---------------------------------------------------------
    def _tick_of(self, angle: float) -> int:
        return int(math.floor(((angle + self._tick_deg / 2.0) % 360.0) / self._tick_deg)) % self._n

    def _raise(self, index: int, reach: float) -> None:
        self._reach[index] = max(self._reach[index], min(1.0, reach))

    def _light(self, index: int) -> bool:
        if self._ticks[index]:
            return False
        self._ticks[index] = True
        return True

    def _fill_between(self, start: int, end: int, arc: float, reach: float) -> bool:
        """Ticks from ``start`` to ``end`` the short way: raised to ``reach``, lit when it is 1."""
        step = 1 if arc > 0 else -1
        lit = False
        index = start
        for _ in range(self._n):
            self._raise(index, reach)
            if reach >= 1.0:
                lit = self._light(index) or lit
            if index == end:
                break
            index = (index + step) % self._n
        return lit

    def _lose_face(self) -> None:
        """A face lost while turned is charged to that direction (once per loss)."""
        prev = self._prev
        if prev is not None and prev.angle is not None and self._state == SWEEPING:
            direction = direction_of_angle(prev.angle)
            self._lost[direction] = self._lost.get(direction, 0) + 1
        self._prev = None

    def _finish(self, t: int, offset, reach: float, angle: Optional[float], reason: Optional[str]) -> SweepStatus:
        if all(self._ticks):
            self._state = DONE
        elif t - self._started >= int(self.cfg.timeout_ms):
            self._state = TIMED_OUT
        self._status = self._snapshot(t, offset, reach, angle, reason)
        return self._status

    def _snapshot(self, t: int, offset, reach: float, angle: Optional[float], reason: Optional[str]) -> SweepStatus:
        hint = None
        if self._state == SWEEPING and t - self._last_fill >= int(self.cfg.hint_after_ms):
            hint = self._largest_gap()
        if self._state == TIMED_OUT:
            hint = self._largest_gap()
        missing = tuple(d for d in GAZE_DIRECTIONS
                        if any(not lit and self.tick_direction(i) == d for i, lit in enumerate(self._ticks)))
        return SweepStatus(
            state=self._state,
            ticks=tuple(self._ticks),
            elapsed_ms=max(0, t - self._started) if self._state != IDLE else 0,
            neutral_deg=self._neutral,
            neutral_sigma_deg=self._neutral_sigma,
            offset_deg=offset,
            reach=float(reach),
            pointer_deg=angle,
            last_reason=reason,
            hint=hint,
            missing=missing,
            lost=dict(self._lost),
            reached_deg=dict(self._reached),
            tick_reach=tuple(self._reach),
            direction_progress={
                d: float(np.mean([self._reach[i] for i in range(self._n) if self.tick_direction(i) == d]))
                for d in GAZE_DIRECTIONS
            },
        )

    def _largest_gap(self) -> Optional[str]:
        """Direction at the middle of the longest run of unlit ticks (ring wraps)."""
        n = self._n
        if all(self._ticks):
            return None
        if not any(self._ticks):
            return None  # nothing lit yet: any direction will do, so point nowhere
        best_start, best_len = 0, 0
        for start in range(n):
            if self._ticks[start] or not self._ticks[(start - 1) % n]:
                continue  # a run starts right after a lit tick
            length = 0
            while length < n and not self._ticks[(start + length) % n]:
                length += 1
            if length > best_len:
                best_start, best_len = start, length
        middle = best_start + (best_len - 1) / 2.0
        return direction_of_angle(middle * self._tick_deg)


def _head_deg(obs: FrameObservation) -> Optional[Tuple[float, float]]:
    if not obs.face_valid:
        return None
    hp = obs.head_pose
    if hp is None:
        return None
    yaw, pitch = float(hp.yaw), float(hp.pitch)
    # An unmeasured pose carries an infinite reprojection error (see preprocess.headpose).
    if not (math.isfinite(yaw) and math.isfinite(pitch) and math.isfinite(float(hp.reprojection_error))):
        return None
    return math.degrees(yaw), math.degrees(pitch)


def _reason(obs: FrameObservation) -> Optional[str]:
    reason = obs.invalid_reason
    if reason is None:
        return None
    return getattr(reason, "value", str(reason))


def summary_line(status: SweepStatus) -> str:
    """One log line: state, ticks, what is missing or lost."""
    parts = [f"{status.state} {status.filled}/{status.total}"]
    if status.missing:
        parts.append("missing=" + ",".join(status.missing))
    if status.lost:
        parts.append("lost=" + ",".join(f"{d}x{n}" for d, n in status.lost.items()))
    return " ".join(parts)
