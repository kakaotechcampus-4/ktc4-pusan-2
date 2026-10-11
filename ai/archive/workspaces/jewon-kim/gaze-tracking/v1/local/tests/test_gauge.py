"""Contracts of the calibration good-frame gauge (``vision.calibration.gauge``).

The gauge fills on usable frames only, pauses (never resets) on bad ones, and
ends full (DONE) or at its timeout (DONE with enough samples, else TIMED_OUT
naming the reason seen most often).  Each test drives one rejection path with
hand-built observations at the 125 ms analysis cadence.
"""

from __future__ import annotations

import dataclasses
import math

import pytest

from vision.calibration.gauge import (
    BLINK,
    HEAD_MOVED,
    LOW_GAZE_CONFIDENCE,
    NOT_ACTIVE,
    OUTLIER,
    SETTLING,
    CalibrationGauge,
    GaugeState,
)
from vision.config import CalibrationConfig
from vision.schemas import FrameObservation, FrameQuality, GazeVector, HeadPose

FRAME_MS = 125


@pytest.fixture
def ccfg() -> CalibrationConfig:
    return CalibrationConfig(target_good_frames=8, min_samples_per_class=5, cue_timeout_ms=4000)


def _obs(t_ms, *, valid=True, reason=None, head_yaw=0.0, head_pitch=0.0, ear=0.3):
    return FrameObservation(
        frame_id=t_ms // FRAME_MS, t_ms=t_ms, face_confidence=1.0, face_valid=valid,
        invalid_reason=reason,
        head_pose=HeadPose(yaw=math.radians(head_yaw), pitch=math.radians(head_pitch)),
        quality=FrameQuality(left_eye_openness=ear, right_eye_openness=ear),
    )


def _gaze(yaw=0.0, pitch=-1.0, confidence=0.9):
    return GazeVector(math.radians(yaw), math.radians(pitch), confidence)


def _settled(gauge: CalibrationGauge, cfg: CalibrationConfig, n_good: int = 0) -> int:
    """Start a cue, wait out the settle window, accept ``n_good`` frames."""
    gauge.start("CAMERA", 0)
    t = 0
    while t < cfg.cue_settle_ms:
        assert gauge.offer(_obs(t), _gaze(), t) == (False, SETTLING)
        t += FRAME_MS
    for _ in range(n_good):
        assert gauge.offer(_obs(t), _gaze(), t) == (True, None)
        t += FRAME_MS
    return t


def test_settling_frames_are_not_counted_and_not_blamed(ccfg):
    gauge = CalibrationGauge(ccfg)
    _settled(gauge, ccfg)
    status = gauge.status
    assert status.good == 0
    assert status.rejected[SETTLING] > 0
    assert status.dominant_reason is None  # the saccade is not the user's fault


def test_the_gauge_fills_to_done_on_good_frames(ccfg):
    gauge = CalibrationGauge(ccfg)
    _settled(gauge, ccfg, n_good=ccfg.target_good_frames)
    status = gauge.status
    assert status.state == GaugeState.DONE.value
    assert status.progress == 1.0 and status.finished
    assert gauge.offer(_obs(9000), _gaze(), 9000) == (False, NOT_ACTIVE)


def test_bad_frames_pause_but_never_reset(ccfg):
    gauge = CalibrationGauge(ccfg)
    t = _settled(gauge, ccfg, n_good=3)
    for _ in range(4):
        assert gauge.offer(_obs(t, valid=False, reason="EYES_CLOSED"), None, t) == (False, "EYES_CLOSED")
        t += FRAME_MS
    assert gauge.status.good == 3
    assert gauge.offer(_obs(t), _gaze(), t) == (True, None)
    assert gauge.status.good == 4


@pytest.mark.parametrize(
    "obs_kwargs, gaze, reason",
    [
        ({"valid": False, "reason": "OUT_OF_FRAME"}, _gaze(), "OUT_OF_FRAME"),
        ({"valid": False}, _gaze(), "NO_FACE"),
        ({}, None, "BACKBONE_FAILED"),
        ({}, _gaze(yaw=math.nan), "BACKBONE_FAILED"),
        ({}, _gaze(confidence=0.1), LOW_GAZE_CONFIDENCE),
        ({"head_yaw": 9.0}, _gaze(), HEAD_MOVED),
        ({"head_pitch": -8.0}, _gaze(), HEAD_MOVED),
    ],
)
def test_each_rejection_is_named(ccfg, obs_kwargs, gaze, reason):
    gauge = CalibrationGauge(ccfg)
    t = _settled(gauge, ccfg, n_good=1)  # pins the head reference at (0, 0)
    assert gauge.offer(_obs(t, **obs_kwargs), gaze, t) == (False, reason)


def test_the_head_reference_carries_over_to_the_next_cue(ccfg):
    gauge = CalibrationGauge(ccfg)
    _settled(gauge, ccfg, n_good=2)
    assert gauge.head_reference == pytest.approx((0.0, 0.0))
    gauge.start("SCREEN", 10_000)
    t = 10_000 + ccfg.cue_settle_ms
    assert gauge.offer(_obs(t, head_pitch=-10.0), _gaze(pitch=-10.0), t) == (False, HEAD_MOVED)


def test_a_blink_against_this_cues_own_eye_openness_is_rejected(ccfg):
    gauge = CalibrationGauge(ccfg, blink_ratio=0.7)
    t = _settled(gauge, ccfg, n_good=4)
    assert gauge.offer(_obs(t, ear=0.15), _gaze(), t) == (False, BLINK)
    # A narrower but steady eye (reading posture) is not a blink.
    assert gauge.offer(_obs(t + FRAME_MS, ear=0.25), _gaze(), t + FRAME_MS) == (True, None)


def test_a_glance_elsewhere_is_an_outlier(ccfg):
    gauge = CalibrationGauge(ccfg)
    t = _settled(gauge, ccfg, n_good=4)
    assert gauge.offer(_obs(t), _gaze(yaw=15.0), t) == (False, OUTLIER)
    assert gauge.offer(_obs(t + FRAME_MS), _gaze(yaw=0.4), t + FRAME_MS) == (True, None)


def test_timeout_with_enough_samples_still_completes(ccfg):
    gauge = CalibrationGauge(ccfg)
    _settled(gauge, ccfg, n_good=ccfg.min_samples_per_class)
    status = gauge.tick(ccfg.cue_timeout_ms)
    assert status.state == GaugeState.DONE.value


def test_timeout_without_enough_samples_fails_with_the_dominant_reason(ccfg):
    gauge = CalibrationGauge(ccfg)
    t = _settled(gauge, ccfg, n_good=2)
    while t < ccfg.cue_timeout_ms:
        gauge.offer(_obs(t, head_yaw=12.0), _gaze(), t)
        t += FRAME_MS
    status = gauge.tick(t)
    assert status.state == GaugeState.TIMED_OUT.value
    assert status.dominant_reason == HEAD_MOVED
    assert "head still" in status.hint


def test_an_offer_past_the_timeout_ends_the_cue(ccfg):
    gauge = CalibrationGauge(ccfg)
    _settled(gauge, ccfg)
    assert gauge.offer(_obs(ccfg.cue_timeout_ms), _gaze(), ccfg.cue_timeout_ms) == (False, NOT_ACTIVE)
    assert gauge.status.state == GaugeState.TIMED_OUT.value


def test_an_idle_gauge_accepts_nothing(ccfg):
    gauge = CalibrationGauge(ccfg)
    assert gauge.offer(_obs(0), _gaze(), 0) == (False, NOT_ACTIVE)
    assert gauge.status.state == GaugeState.IDLE.value


def test_progress_tracks_good_frames_over_target(ccfg):
    gauge = CalibrationGauge(dataclasses.replace(ccfg, target_good_frames=10))
    _settled(gauge, ccfg, n_good=4)
    assert gauge.status.progress == pytest.approx(0.4)


def test_a_cue_can_take_its_own_target_and_be_extended(ccfg):
    gauge = CalibrationGauge(ccfg)
    gauge.start("SCREEN", 0, target=4)
    t = 0
    while t < ccfg.cue_settle_ms:
        gauge.offer(_obs(t), _gaze(), t)
        t += FRAME_MS
    for _ in range(4):
        gauge.offer(_obs(t), _gaze(), t)
        t += FRAME_MS
    assert gauge.status.state == "DONE" and gauge.status.target == 4
    gauge.extend(ccfg.target_good_frames)  # a done cue reopens, keeping its frames
    assert gauge.status.state == "COLLECTING" and (gauge.status.good, gauge.status.target) == (4, 8)
    for _ in range(4):
        gauge.offer(_obs(t), _gaze(), t)
        t += FRAME_MS
    assert gauge.status.state == "DONE" and gauge.status.good == 8
    gauge.start("CAMERA", t)
    assert gauge.status.target == ccfg.target_good_frames  # the next cue is back to the default
