"""Head circle check (``gaze_lab.runtime.sweep``): the ring fills where the head went.

The ring is presenter-centric like ``GAZE_DIRECTIONS``: tick 0 is centred on
the presenter's RIGHT, which in the raw frame is a NEGATIVE yaw.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from gaze_lab.config import SweepConfig
from gaze_lab.runtime.session import VisionSession
from gaze_lab.runtime.sweep import (
    CENTERING,
    DONE,
    NO_HEAD_POSE,
    SWEEPING,
    TIMED_OUT,
    TOO_FAST,
    HeadSweep,
    direction_of_angle,
)
from gaze_lab.schemas import GAZE_DIRECTIONS, FaceScene, FrameObservation, FrameQuality, HeadPose, InvalidReason

FRAME_MS = 125
FRAME = np.zeros((4, 4, 3), dtype=np.uint8)
#: A laptop webcam sees a presenter looking at the screen chin-up.
CENTRE = (1.0, 14.0)


def _obs(t_ms, yaw=CENTRE[0], pitch=CENTRE[1], *, valid=True, measured=True):
    return FrameObservation(
        frame_id=t_ms // FRAME_MS, t_ms=t_ms, face_confidence=1.0 if valid else 0.0, face_valid=valid,
        invalid_reason=None if valid else InvalidReason.NO_FACE,
        head_pose=HeadPose(yaw=math.radians(yaw), pitch=math.radians(pitch),
                           reprojection_error=0.0 if measured else math.inf),
        quality=FrameQuality(face_brightness=120.0, background_brightness=110.0),
        face_bbox=(250, 150, 140, 180) if valid else None, image_size=(640, 480),
        scene=FaceScene(n_faces=1 if valid else 0, second_face_area_ratio=0.0, iris_diameter_px=11.0),
    )


def _turned(right, up):
    """Head pose for a turn of ``right`` / ``up`` degrees from CENTRE (presenter-centric)."""
    return CENTRE[0] - right, CENTRE[1] + up


def _started(cfg=None, t_ms=0):
    sweep = HeadSweep(cfg or SweepConfig())
    sweep.start(t_ms)
    for i in range(sweep.cfg.neutral_frames):
        status = sweep.offer(_obs(t_ms + i * FRAME_MS), t_ms + i * FRAME_MS)
    assert status.state == SWEEPING
    return sweep, t_ms + sweep.cfg.neutral_frames * FRAME_MS


def _circle(sweep, t_ms, *, seconds=3.0, scale=1.3, turns=1.0, start_deg=0.0):
    """A slow circle on the reach ellipse, at 8 FPS."""
    cfg = sweep.cfg
    n = int(seconds * 1000 / FRAME_MS)
    status = sweep.status
    for i in range(n + 1):
        a = math.radians(start_deg + 360.0 * turns * i / n)
        right = scale * cfg.reach_yaw_deg * math.cos(a)
        up = scale * cfg.reach_pitch_deg * math.sin(a)
        status = sweep.offer(_obs(t_ms, *_turned(right, up)), t_ms)
        t_ms += FRAME_MS
    return status, t_ms


def _go(sweep, t_ms, right, up, steps=3):
    """Turn from the centre to ``(right, up)`` over a few frames, as a person would."""
    status = sweep.status
    for k in range(1, steps + 1):
        status = sweep.offer(_obs(t_ms, *_turned(right * k / steps, up * k / steps)), t_ms)
        t_ms += FRAME_MS
    return status, t_ms


def _lit_directions(status, sweep):
    return {sweep.tick_direction(i) for i, lit in enumerate(status.ticks) if lit}


# ==========================================================================
# Filling the ring
# ==========================================================================


def test_a_slow_circle_fills_every_tick():
    sweep, t = _started()
    status, _ = _circle(sweep, t)
    assert status.state == DONE and status.finished
    assert status.filled == status.total == 32
    assert status.missing == ()
    assert set(status.reached_deg) == set(GAZE_DIRECTIONS)
    assert status.neutral_deg == pytest.approx(CENTRE)


def test_the_centre_is_the_median_head_pose_not_zero():
    sweep, t = _started()
    for _ in range(16):  # holding the centre pose (chin-up at a laptop) lights nothing
        status = sweep.offer(_obs(t), t)
        t += FRAME_MS
    assert status.filled == 0
    assert status.offset_deg == pytest.approx((0.0, 0.0))
    assert status.pointer_deg is None


def test_turning_to_the_presenters_right_lights_the_right_ticks():
    sweep, t = _started()
    status, t = _go(sweep, t, 20.0, 0.0)  # yaw goes NEGATIVE
    assert status.offset_deg[0] > 0
    assert _lit_directions(status, sweep) == {"RIGHT"}


def test_up_and_diagonals_follow_the_presenters_view():
    for right, up, expected in [(0.0, 12.0, "UP"), (-20.0, 0.0, "LEFT"), (0.0, -12.0, "DOWN"),
                                (14.0, 9.0, "UP_RIGHT"), (-14.0, -9.0, "DOWN_LEFT")]:
        sweep, t = _started()
        status, t = _go(sweep, t, right, up)
        assert _lit_directions(status, sweep) == {expected}, (right, up)


def test_a_small_turn_lights_nothing_but_fills_that_directions_gauge():
    sweep, t = _started()
    status = sweep.offer(_obs(t, *_turned(10.0, 0.0)), t)  # 10 deg < reach 14 deg
    assert status.reach == pytest.approx(10.0 / 14.0)
    assert status.filled == 0 and status.pointer_deg is not None
    assert status.tick_reach[0] == pytest.approx(10.0 / 14.0)
    assert status.direction_progress["RIGHT"] == pytest.approx(10.0 / 14.0 / 4)  # one of its 4 ticks
    assert status.direction_progress["LEFT"] == 0.0


def test_each_direction_gauge_is_full_once_its_ticks_are_lit():
    sweep, t = _started()
    status, _ = _circle(sweep, t)
    assert all(v == pytest.approx(1.0) for v in status.direction_progress.values())
    assert all(r == pytest.approx(1.0) for r in status.tick_reach)


def test_eight_fps_fills_the_ticks_between_two_turned_frames():
    sweep, t = _started()
    cfg = sweep.cfg
    r = 1.2
    for k, deg in enumerate((0.0, 40.0)):  # 40 deg apart in one frame: ticks 0..4
        a = math.radians(deg)
        status = sweep.offer(_obs(t, *_turned(r * cfg.reach_yaw_deg * math.cos(a),
                                              r * cfg.reach_pitch_deg * math.sin(a))), t)
        t += FRAME_MS
    lit = [i for i, on in enumerate(status.ticks) if on]
    assert lit == [0, 1, 2, 3, 4]


def test_a_jerk_lights_nothing_and_asks_for_a_slower_turn():
    sweep, t = _started()
    status = sweep.offer(_obs(t, *_turned(40.0, 0.0)), t)  # 40 deg in 125 ms = 320 deg/s
    assert status.last_reason == TOO_FAST and status.filled == 0
    # The next frame, now slow, lights its own tick but does not bridge the jerk.
    t += FRAME_MS
    status = sweep.offer(_obs(t, *_turned(40.0, 5.0)), t)
    assert status.last_reason is None
    assert status.filled == 1


def test_losing_the_face_keeps_progress_and_is_charged_to_the_direction():
    sweep, t = _started()
    status, t = _go(sweep, t, -22.0, 0.0)
    before = status.filled
    for _ in range(3):  # three frames without a face: one loss, not three
        status = sweep.offer(_obs(t, valid=False), t)
        t += FRAME_MS
    assert status.last_reason == "NO_FACE"
    assert status.filled == before > 0
    assert status.lost == {"LEFT": 1}


def test_an_unmeasured_head_pose_pauses_the_ring():
    sweep, t = _started()
    status = sweep.offer(_obs(t, *_turned(8.0, 0.0), measured=False), t)
    assert status.last_reason == NO_HEAD_POSE and status.filled == 0


def test_a_stalled_ring_points_at_the_largest_gap():
    sweep, t = _started()
    status, t = _circle(sweep, t, seconds=1.5, turns=0.5)  # the upper half only
    assert status.hint is None
    for _ in range(int(sweep.cfg.hint_after_ms / FRAME_MS) + 1):
        status = sweep.offer(_obs(t), t)
        t += FRAME_MS
    assert status.hint == "DOWN"
    assert set(status.missing) >= {"DOWN_LEFT", "DOWN", "DOWN_RIGHT"}


def test_the_ring_times_out_naming_what_is_missing():
    cfg = SweepConfig(timeout_ms=4000)
    sweep, t = _started(cfg)
    _, t = _go(sweep, t, 20.0, 0.0)
    while not sweep.status.finished:  # stuck on the right
        sweep.offer(_obs(t, *_turned(20.0, 0.0)), t)
        t += FRAME_MS
    status = sweep.status
    assert status.state == TIMED_OUT
    assert status.ticks[0] and status.filled == 1
    # A direction is missing while any of its ticks is unlit.
    assert status.missing == GAZE_DIRECTIONS
    assert status.hint == "LEFT"  # the middle of the one big gap
    # A finished ring ignores further frames.
    assert sweep.offer(_obs(t, *_turned(-20.0, 0.0)), t) is status


def test_without_a_face_the_ring_never_finds_its_centre():
    sweep = HeadSweep(SweepConfig())
    sweep.start(0)
    for i in range(8):
        status = sweep.offer(_obs(i * FRAME_MS, valid=False), i * FRAME_MS)
    assert status.state == CENTERING and status.neutral_deg is None
    assert status.lost == {}


def test_the_ring_resolution_must_split_into_the_eight_directions():
    with pytest.raises(ValueError):
        HeadSweep(SweepConfig(ticks=30))


def test_every_direction_owns_the_same_number_of_ticks():
    sweep = HeadSweep(SweepConfig())
    owners = [sweep.tick_direction(i) for i in range(32)]
    assert all(owners.count(d) == 4 for d in GAZE_DIRECTIONS)
    assert owners[0] == "RIGHT" and owners[8] == "UP" and owners[16] == "LEFT" and owners[24] == "DOWN"
    assert direction_of_angle(359.0) == "RIGHT" and direction_of_angle(90.0) == "UP"


# ==========================================================================
# Session
# ==========================================================================


class HeadPipeline:
    """Reports a face whose head pose the test steers; no MediaPipe."""

    def __init__(self) -> None:
        self.yaw, self.pitch = CENTRE

    def process_bgr(self, bgr, frame_id, t_ms, *, main_face_hint=None):
        obs = _obs(int(t_ms), self.yaw, self.pitch)
        obs.frame_id = int(frame_id)
        return obs

    def close(self) -> None:
        pass


def test_the_session_runs_the_ring_without_touching_calibration(fresh_cfg):
    from gaze_lab.backbones.head import HeadPoseBackbone

    pipeline = HeadPipeline()
    with VisionSession(fresh_cfg, backbone=HeadPoseBackbone(), pipeline=pipeline) as sess:
        assert sess.start_head_sweep(0).state == CENTERING
        t = 0
        cfg = fresh_cfg.sweep
        for i in range(cfg.neutral_frames + 25):
            if i >= cfg.neutral_frames:
                a = 2 * math.pi * (i - cfg.neutral_frames) / 24
                right, up = 1.3 * cfg.reach_yaw_deg * math.cos(a), 1.3 * cfg.reach_pitch_deg * math.sin(a)
                pipeline.yaw, pipeline.pitch = _turned(right, up)
            status = sess.offer_sweep_frame(FRAME, t)
            t += FRAME_MS
        assert status.state == DONE
        assert sess.head_sweep is status
        assert sess.calibration_counts == {"CAMERA": 0, "SCREEN": 0, "BOTTOM": 0}
        assert not sess.is_calibrated


def test_the_centre_is_a_second_of_looking_at_the_screen_with_its_noise():
    sweep = HeadSweep(SweepConfig())
    sweep.start(0)
    assert SweepConfig().neutral_frames == 8  # 1 s at 8 FPS
    wobble = [-0.4, 0.2, 0.0, 0.4, -0.2, 0.1, -0.1, 0.3]
    status = None
    for i, w in enumerate(wobble):
        status = sweep.offer(_obs(i * FRAME_MS, CENTRE[0] + w, CENTRE[1] - w), i * FRAME_MS)
    assert status.state == SWEEPING
    assert status.neutral_deg == pytest.approx((CENTRE[0] + 0.05, CENTRE[1] - 0.05))
    mad = float(np.median(np.abs(np.asarray(wobble) - 0.05)))
    assert status.neutral_sigma_deg == pytest.approx((1.4826 * mad, 1.4826 * mad))
    assert status.to_dict()["neutral_sigma_deg"] == pytest.approx([1.4826 * mad, 1.4826 * mad])
