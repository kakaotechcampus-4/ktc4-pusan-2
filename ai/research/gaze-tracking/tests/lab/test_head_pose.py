"""Contracts of the head-pose default (``gaze_lab.backbones.head``) and what it changes.

The default backbone reads where the face points; the eye backbones are parked
in ``gaze_lab.eye``.  Locked in here:

* the head backbone returns the head angles unchanged, refuses a head pose that
  was never measured, and declares that it does not read the eyes;
* calibration lets the head move between targets and ignores blinks when the
  backbone does not read the eyes (and still holds the head for one that does);
* the live condition monitor measures head drift from the *nearest* calibrated
  posture, so dipping toward the calibrated script is not a drift;
* a SCREEN anchor that coincides with the lens is folded into CAMERA instead of
  failing the calibration;
* an unreadable camera position does not block a strict take when the head is
  the signal, while a position read as unsupported still does.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from gaze_lab.backbones.head import HeadPoseBackbone
from gaze_lab.backbones.registry import get_backbone_class
from gaze_lab.calibration.gauge import BLINK, HEAD_MOVED, SETTLING, CalibrationGauge
from gaze_lab.calibration.references import SCREEN_MERGED_WARNING, ReferenceAnchorClassifier
from gaze_lab.config import BackboneConfig, CalibrationConfig, ConditionConfig, load_config
from gaze_lab.runtime.condition import ConditionIssue, ConditionMonitor, SceneBaselineAccumulator
from gaze_lab.runtime.policy import gate_placement
from gaze_lab.runtime.session import VisionSession
from gaze_lab.schemas import (
    CalibrationFailReason,
    CalibrationSample,
    FaceScene,
    FrameObservation,
    FrameQuality,
    GazeVector,
    HeadPose,
)

FRAME_MS = 125
FRAME = np.zeros((4, 4, 3), dtype=np.uint8)


def _turned_bbox(yaw, pitch, bbox=(250, 150, 140, 180), iris_px=11.0, radius_cm=8.0):
    """Where the face lands in the picture when the head turns in place: the face sits
    ~8 cm in front of the head's rotation centre, so it slides toward the turn."""
    px_per_cm = iris_px / 1.17
    a, b = math.radians(yaw), math.radians(pitch)
    dx = radius_cm * math.sin(a) * math.cos(b) * px_per_cm
    dy = -radius_cm * math.sin(b) * px_per_cm
    return (bbox[0] + dx, bbox[1] + dy, bbox[2], bbox[3])


def _obs(t_ms=0, *, yaw=0.0, pitch=0.0, ear=0.3, valid=True):
    return FrameObservation(
        frame_id=t_ms // FRAME_MS, t_ms=t_ms, face_confidence=1.0, face_valid=valid,
        head_pose=HeadPose(yaw=math.radians(yaw), pitch=math.radians(pitch)),
        quality=FrameQuality(face_brightness=120.0, background_brightness=110.0,
                             left_eye_openness=ear, right_eye_openness=ear),
        face_bbox=_turned_bbox(yaw, pitch), image_size=(640, 480),
        scene=FaceScene(n_faces=1, second_face_area_ratio=0.0, iris_diameter_px=11.0),
    )


# ==========================================================================
# The backbone
# ==========================================================================


def test_the_head_backbone_returns_the_head_angles_unchanged():
    head = HeadPose(yaw=math.radians(12.0), pitch=math.radians(-7.0), roll=0.3)
    gaze = HeadPoseBackbone().predict(None, None, None, head)
    assert gaze.gaze_yaw == pytest.approx(head.yaw)
    assert gaze.gaze_pitch == pytest.approx(head.pitch)
    assert gaze.confidence == 1.0
    assert gaze.backbone == "head_pose"


def test_confidence_is_the_share_of_landmarks_inside_the_frame():
    landmarks = np.full((478, 3), 0.5)
    landmarks[:239, 0] = 1.4  # half the face slid out of the picture
    gaze = HeadPoseBackbone().predict(None, None, None, HeadPose(), landmarks=landmarks)
    assert gaze.confidence == pytest.approx(239 / 478)


@pytest.mark.parametrize(
    "head",
    [
        None,
        HeadPose(reprojection_error=math.inf, depth_proxy=math.nan),  # failed PnP keeps zero angles
        HeadPose(yaw=math.nan),
    ],
)
def test_an_unmeasured_head_pose_is_refused_not_read_as_frontal(head):
    with pytest.raises(ValueError):
        HeadPoseBackbone().predict(None, None, None, head)


def test_head_pose_is_the_default_and_the_eye_backbones_are_parked():
    assert BackboneConfig().name == "head_pose"
    assert load_config().backbone.name == "head_pose"
    assert get_backbone_class("head_pose") is HeadPoseBackbone
    assert HeadPoseBackbone.uses_eyes is False
    for name in ("mediapipe_geom", "l2cs", "gazetr"):
        cls = get_backbone_class(name)
        assert cls.uses_eyes is True
        assert cls.__module__.startswith("gaze_lab.eye.")


# ==========================================================================
# Calibration gauge
# ==========================================================================


@pytest.fixture
def gcfg() -> CalibrationConfig:
    return CalibrationConfig(target_good_frames=8, min_samples_per_class=5, cue_timeout_ms=4000)


def _accept_lens_frames(gauge, cfg, n=4):
    gauge.start("CAMERA", 0)
    t = 0
    while t < cfg.cue_settle_ms:
        assert gauge.offer(_obs(t), GazeVector(0.0, 0.0, 0.9), t)[1] == SETTLING
        t += FRAME_MS
    for _ in range(n):
        assert gauge.offer(_obs(t), GazeVector(0.0, 0.0, 0.9), t) == (True, None)
        t += FRAME_MS
    return t


def test_without_eyes_the_head_may_move_to_the_next_target(gcfg):
    gauge = CalibrationGauge(gcfg, eye_based=False)
    _accept_lens_frames(gauge, gcfg)
    gauge.start("BOTTOM", 10_000)
    t = 10_000 + gcfg.cue_settle_ms
    dipped = GazeVector(0.0, math.radians(-18.0), 0.9)
    assert gauge.offer(_obs(t, pitch=-18.0), dipped, t) == (True, None)


def test_without_eyes_a_blink_does_not_cost_a_frame(gcfg):
    gauge = CalibrationGauge(gcfg, eye_based=False)
    t = _accept_lens_frames(gauge, gcfg)
    assert gauge.offer(_obs(t, ear=0.13), GazeVector(0.0, 0.0, 0.9), t) == (True, None)


def test_an_eye_backbone_still_holds_the_head_and_screens_blinks(gcfg):
    gauge = CalibrationGauge(gcfg, eye_based=True)
    t = _accept_lens_frames(gauge, gcfg)
    assert gauge.offer(_obs(t, ear=0.13), GazeVector(0.0, 0.0, 0.9), t) == (False, BLINK)
    gauge.start("BOTTOM", 10_000)
    t = 10_000 + gcfg.cue_settle_ms
    dipped = GazeVector(0.0, math.radians(-18.0), 0.9)
    assert gauge.offer(_obs(t, pitch=-18.0), dipped, t) == (False, HEAD_MOVED)


# ==========================================================================
# Condition monitor: nearest calibrated posture
# ==========================================================================


def _cue_baseline():
    acc = SceneBaselineAccumulator()
    for i in range(8):
        acc.add(_obs(i * FRAME_MS, pitch=0.0), cue="CAMERA")
        acc.add(_obs(i * FRAME_MS, pitch=-20.0), cue="BOTTOM")
    return acc.build()


def test_the_baseline_keeps_one_head_posture_per_cue():
    base = _cue_baseline()
    assert base.head_poses["CAMERA"] == pytest.approx((0.0, 0.0))
    assert base.head_poses["BOTTOM"] == pytest.approx((0.0, -20.0))
    assert base.head_deviation(0.0, -19.0) == pytest.approx(1.0)
    assert base.head_deviation(0.0, -10.0) == pytest.approx(10.0)


def test_frames_without_a_cue_fall_back_to_the_overall_pose():
    acc = SceneBaselineAccumulator()
    for i in range(6):
        acc.add(_obs(i * FRAME_MS, yaw=4.0, pitch=-3.0))
    base = acc.build()
    assert base.head_poses == {}
    assert base.head_deviation(4.0, 7.0) == pytest.approx(10.0)


def test_dipping_toward_the_calibrated_script_is_not_a_drift():
    monitor = ConditionMonitor(ConditionConfig(), _cue_baseline())
    for i in range(8):
        monitor.update(_obs(i * FRAME_MS))
    reading = monitor.update(_obs(8 * FRAME_MS, pitch=-20.0))
    assert ConditionIssue.HEAD_TURNED.value not in reading.issues
    assert reading.components["head"] == 1.0
    turned = monitor.update(_obs(9 * FRAME_MS, yaw=25.0, pitch=-10.0))
    assert ConditionIssue.HEAD_TURNED.value in turned.issues


def test_a_re_anchor_moves_every_calibrated_posture_with_the_lens():
    moved = _cue_baseline().shifted_head_poses((2.0, 3.0))
    assert moved["CAMERA"] == pytest.approx((2.0, 3.0))
    assert moved["BOTTOM"] == pytest.approx((2.0, -17.0))


# ==========================================================================
# SCREEN folded into CAMERA
# ==========================================================================


def _cloud(label, centre, n=16, sd=0.6, seed=0):
    rng = np.random.default_rng(seed)
    return [
        CalibrationSample(
            label=label,
            gaze=GazeVector(math.radians(centre[0] + rng.normal(0, sd)),
                            math.radians(centre[1] + rng.normal(0, sd)), 0.9),
            head_pose=HeadPose(),
        )
        for _ in range(n)
    ]


def _three(screen_at, bottom=(0.6, -18.0)):
    return _cloud("CAMERA", (0.5, -1.0), seed=1) + _cloud("BOTTOM", bottom, seed=2) + _cloud(
        "SCREEN", screen_at, seed=3
    )


def test_a_screen_anchor_on_the_lens_is_folded_into_camera():
    cfg = CalibrationConfig(method="reference")
    clf, quality = ReferenceAnchorClassifier.fit(_three(screen_at=(0.6, -1.4)), cfg)

    assert quality.ok, quality
    assert SCREEN_MERGED_WARNING in quality.warnings
    assert clf.classes == ("CAMERA", "BOTTOM", "OTHER")
    assert quality.n_screen == 16
    assert "CAMERA-SCREEN" in quality.pair_separation  # the report still shows why
    look_at_screen = clf.decide(_obs(), GazeVector(math.radians(0.6), math.radians(-1.4), 0.9))
    assert look_at_screen.label == "CAMERA"


def test_a_separable_screen_keeps_its_own_class():
    clf, quality = ReferenceAnchorClassifier.fit(_three(screen_at=(0.8, -10.5)), CalibrationConfig())
    assert quality.ok and SCREEN_MERGED_WARNING not in quality.warnings
    assert "SCREEN" in clf.classes


def test_folding_screen_cannot_rescue_a_lens_that_matches_the_script():
    _, quality = ReferenceAnchorClassifier.fit(
        _three(screen_at=(0.6, -1.4), bottom=(0.5, -1.5)), CalibrationConfig()
    )
    assert quality.reason == CalibrationFailReason.CLASS_NOT_SEPARABLE.value
    assert SCREEN_MERGED_WARNING not in quality.warnings


# ==========================================================================
# Placement gate
# ==========================================================================


def _placement(placement, reason="OK", supported=False):
    return SimpleNamespace(placement=placement, reason=reason, supported=supported, hint="hint")


def test_an_unreadable_camera_position_does_not_block_a_head_take():
    inconclusive = _placement("INCONCLUSIVE", reason="TARGETS_NOT_SEPARATED")
    assert gate_placement(inconclusive, strict=True, block_inconclusive=False).allowed
    assert gate_placement(None, strict=True, block_inconclusive=False).allowed
    assert not gate_placement(inconclusive, strict=True).allowed  # eye default: still blocks


def test_a_camera_read_as_below_the_screen_still_blocks():
    below = _placement("BOTTOM")
    assert not gate_placement(below, strict=True, block_inconclusive=False).allowed


# ==========================================================================
# Session: a natural (head-moving) calibration end to end
# ==========================================================================


class HeadPipeline:
    """Reports a face whose head pose the test steers; no MediaPipe."""

    def __init__(self) -> None:
        self.yaw = 0.0
        self.pitch = 0.0
        #: Where the presenter sits, px from the calibration position (image axes).
        self.moved = (0.0, 0.0)

    def process_bgr(self, bgr, frame_id, t_ms, *, main_face_hint=None):
        obs = _obs(int(t_ms), yaw=self.yaw, pitch=self.pitch)
        x, y, w, h = obs.face_bbox
        obs.face_bbox = (x + self.moved[0], y + self.moved[1], w, h)
        obs.frame_id = int(frame_id)
        return obs

    def close(self) -> None:
        pass


#: Head postures for the three looks: the presenter dips toward the script.
POSTURES = {"CAMERA": (0.5, 18.0), "SCREEN": (0.8, 11.0), "BOTTOM": (0.6, 4.0)}


@pytest.fixture
def head_session(fresh_cfg):
    pipeline = HeadPipeline()
    sess = VisionSession(fresh_cfg, backbone=HeadPoseBackbone(), pipeline=pipeline)
    yield sess, pipeline
    sess.close()


def _calibrate(sess, pipeline):
    t_ms = 0
    for cue in ("CAMERA", "SCREEN", "BOTTOM"):
        sess.start_calibration_cue(cue, t_ms)
        status = None
        for i in range(60):
            jitter = ((i % 5) - 2) * 0.3
            pipeline.yaw, pipeline.pitch = POSTURES[cue][0] + jitter, POSTURES[cue][1] + jitter
            status = sess.offer_calibration_frame(FRAME, t_ms)
            t_ms += FRAME_MS
            if status.finished:
                break
        assert status.state == "DONE", status
        assert HEAD_MOVED not in status.rejected
    return sess.finish_calibration(), t_ms


def test_a_session_on_the_head_backbone_calibrates_with_the_head_moving(head_session):
    sess, pipeline = head_session
    assert sess.eye_based is False

    quality, t_ms = _calibrate(sess, pipeline)
    assert quality.ok, quality
    assert sess.smoother.classes == ("CAMERA", "SCREEN", "BOTTOM", "OTHER")

    pipeline.yaw, pipeline.pitch = POSTURES["BOTTOM"]
    decision = None
    for _ in range(12):
        _event, decision = sess.process_frame(FRAME, t_ms)
        t_ms += FRAME_MS
    assert decision.label == "BOTTOM"
    assert ConditionIssue.HEAD_TURNED.value not in sess.last_condition.issues


def test_the_session_records_gaze_evidence_for_the_agents(head_session):
    sess, pipeline = head_session
    quality, t_ms = _calibrate(sess, pipeline)
    assert quality.ok, quality

    pipeline.yaw, pipeline.pitch = POSTURES["BOTTOM"]
    for _ in range(int(5000 / FRAME_MS)):  # five seconds on the script
        sess.process_frame(FRAME, t_ms)
        t_ms += FRAME_MS
    pipeline.yaw, pipeline.pitch = POSTURES["CAMERA"][0] - 40.0, POSTURES["CAMERA"][1]
    for _ in range(int(3000 / FRAME_MS)):  # three seconds turned toward the image left
        sess.process_frame(FRAME, t_ms)
        t_ms += FRAME_MS

    evidence = sess.gaze_evidence
    states = [s.state for s in evidence.timeline.samples]
    assert states.count("BOTTOM") >= 4 and "OTHER" in states
    summary = evidence.summary()
    script = [p for p in summary["problem_segments"] if p["issue_type"] == "GAZE_ON_SCRIPT"]
    assert script and script[0]["duration_ms"] >= 4000
    away = evidence.issues()
    assert away[0]["issue_type"] == "GAZE_AWAY"
    # Image-left (yaw < 0) is the presenter's RIGHT.
    assert away[0]["evidence"]["direction"] == "RIGHT"


# ==========================================================================
# Calibration cues read against the head circle; a turned head is not a condition
# ==========================================================================


def _gate_gauge(reference, cue):
    from gaze_lab.config import CalibrationConfig

    gauge = CalibrationGauge(CalibrationConfig(), eye_based=False)
    gauge.set_direction_reference(reference)
    gauge.start(cue, 0)
    return gauge


def _offer(gauge, t_ms, yaw, pitch):
    obs = _obs(t_ms, yaw=yaw, pitch=pitch)
    return gauge.offer(obs, GazeVector(obs.head_pose.yaw, obs.head_pose.pitch, 1.0), t_ms)


def test_a_cue_fills_only_while_the_head_points_its_way():
    from gaze_lab.calibration.gauge import LOOK_HIGHER, LOOK_LOWER, OFF_TARGET

    centre = (1.0, 14.0)  # the head circle's centre: looking at the screen
    lens = _gate_gauge(centre, "CAMERA")
    assert _offer(lens, 600, 1.0, 14.0) == (False, LOOK_HIGHER)  # did not look up at all
    assert _offer(lens, 725, 1.0, 17.0) == (True, None)
    assert _offer(lens, 850, -12.0, 17.0) == (False, OFF_TARGET)  # up, but far to the side
    assert lens.status.good == 1

    script = _gate_gauge(centre, "BOTTOM")
    assert _offer(script, 600, 1.0, 12.5) == (False, LOOK_LOWER)  # 1.5 deg is not toward the script
    assert _offer(script, 725, 1.0, 9.0) == (True, None)

    screen = _gate_gauge(centre, "SCREEN")
    assert _offer(screen, 600, 1.0, 22.5) == (False, OFF_TARGET)
    assert _offer(screen, 725, 2.0, 13.0) == (True, None)


def test_without_a_reference_or_with_an_eye_backbone_the_direction_is_not_checked():
    from gaze_lab.config import CalibrationConfig

    assert _offer(_gate_gauge(None, "CAMERA"), 600, 1.0, 14.0) == (True, None)
    eye = CalibrationGauge(CalibrationConfig(), eye_based=True)
    eye.set_direction_reference((1.0, 14.0))
    eye.start("CAMERA", 0)
    assert _offer(eye, 600, 1.0, 14.0) == (True, None)


def test_the_session_reads_the_cues_from_the_head_circle_else_the_screen_look(head_session):
    sess, pipeline = head_session
    # No circle: the lens and screen looks are free, the script is read from the screen look.
    sess.start_calibration_cue("CAMERA", 0)
    assert sess._gauge.direction_reference is None
    pipeline.yaw, pipeline.pitch = POSTURES["SCREEN"]
    sess.start_calibration_cue("SCREEN", 0)
    t = 0
    for _ in range(30):
        status = sess.offer_calibration_frame(FRAME, t)
        t += FRAME_MS
        if status.finished:
            break
    sess.start_calibration_cue("BOTTOM", t)
    assert sess._gauge.direction_reference == pytest.approx(POSTURES["SCREEN"], abs=0.01)

    # After the circle and before the screen-centre look, the cues are read from its centre.
    sess.reset_calibration()
    pipeline.yaw, pipeline.pitch = 2.0, 13.0
    sess.start_head_sweep(t)
    for _ in range(sess.cfg.sweep.neutral_frames):
        t += FRAME_MS
        sess.offer_sweep_frame(FRAME, t)
    sess.start_calibration_cue("CAMERA", t)
    assert sess._gauge.direction_reference == pytest.approx((2.0, 13.0))


def _circle_centre(sess, pipeline, posture, t):
    """The head circle's first second: its centre is this posture."""
    pipeline.yaw, pipeline.pitch = posture
    sess.start_head_sweep(t)
    for _ in range(sess.cfg.sweep.neutral_frames):
        t += FRAME_MS
        sess.offer_sweep_frame(FRAME, t)
    assert sess.head_sweep.neutral_deg == pytest.approx(posture)
    return t


def _hold(sess, t, n=60):
    status = None
    for _ in range(n):
        t += FRAME_MS
        status = sess.offer_calibration_frame(FRAME, t)
        if status.finished:
            break
    return status, t


def test_the_screen_look_confirms_the_circle_centre_and_folds_it_in(head_session):
    sess, pipeline = head_session
    cal = sess.cfg.calibration
    t = _circle_centre(sess, pipeline, POSTURES["SCREEN"], 0)
    sess.start_calibration_cue("SCREEN", t)
    assert sess._gauge.status.target == cal.cue_confirm_frames  # a confirmation, not a measurement
    status, t = _hold(sess, t)
    assert status.state == "DONE" and status.good == cal.cue_confirm_frames
    check = sess.baseline_check
    assert check.confirmed and check.shift_deg == pytest.approx(0.0, abs=1e-6)
    assert check.seeded == sess.cfg.sweep.neutral_frames
    screen = [s for s in sess._samples if s.label == "SCREEN"]
    assert len(screen) == cal.cue_confirm_frames + sess.cfg.sweep.neutral_frames
    sess.start_calibration_cue("CAMERA", t)
    assert sess._gauge.direction_reference == pytest.approx(POSTURES["SCREEN"], abs=0.01)


def test_a_presenter_who_moved_since_the_circle_is_measured_again(head_session):
    sess, pipeline = head_session
    cal = sess.cfg.calibration
    t = _circle_centre(sess, pipeline, POSTURES["SCREEN"], 0)
    moved = (POSTURES["SCREEN"][0], POSTURES["SCREEN"][1] + 5.0)  # a cushion later: 5 deg higher
    pipeline.yaw, pipeline.pitch = moved
    sess.start_calibration_cue("SCREEN", t)
    status, t = _hold(sess, t)
    assert status.state == "DONE" and status.good == cal.target_good_frames  # measured in full
    check = sess.baseline_check
    assert not check.confirmed and check.shift_deg == pytest.approx(5.0, abs=1e-6) and check.seeded == 0
    screen = [s for s in sess._samples if s.label == "SCREEN"]
    assert len(screen) == cal.target_good_frames
    # The lens and script looks are read from where the presenter is now.
    sess.start_calibration_cue("BOTTOM", t)
    assert sess._gauge.direction_reference == pytest.approx(moved, abs=0.01)


def test_without_a_circle_the_screen_look_is_a_full_measurement(head_session):
    sess, pipeline = head_session
    pipeline.yaw, pipeline.pitch = POSTURES["SCREEN"]
    sess.start_calibration_cue("SCREEN", 0)
    assert sess._gauge.status.target == sess.cfg.calibration.target_good_frames
    status, _ = _hold(sess, 0)
    assert status.state == "DONE" and sess.baseline_check is None


def test_turning_the_head_away_is_a_direction_not_a_worse_measurement(head_session):
    sess, pipeline = head_session
    quality, t_ms = _calibrate(sess, pipeline)
    assert quality.ok

    pipeline.yaw, pipeline.pitch = POSTURES["CAMERA"][0] - 40.0, POSTURES["CAMERA"][1] + 15.0
    for _ in range(8):
        _event, decision = sess.process_frame(FRAME, t_ms)
        t_ms += FRAME_MS
    assert decision.label == "OTHER" and decision.direction is not None
    assert sess.last_condition.reliability == 1.0
    assert ConditionIssue.HEAD_TURNED.value not in sess.last_condition.issues


def test_moving_far_from_the_calibration_position_stops_the_judgement(head_session):
    sess, pipeline = head_session
    quality, t_ms = _calibrate(sess, pipeline)
    assert quality.ok

    pipeline.yaw, pipeline.pitch = POSTURES["CAMERA"]
    pipeline.moved = (70.0, 0.0)  # ~7 cm to the side at ~42 cm: beyond the 7 deg cue gap
    for _ in range(24):
        _event, decision = sess.process_frame(FRAME, t_ms)
        t_ms += FRAME_MS
    condition = sess.last_condition
    assert condition.severe and condition.issues[0] == ConditionIssue.MOVED_TOO_FAR.value
    assert condition.drift["position_deg"] > condition.drift["fail_deg"]
    assert decision.label == "UNCERTAIN" and decision.uncertain_reason == "MOVED_TOO_FAR"


# ==========================================================================
# A little past the calibrated area is the nearest target; a clear look away is OTHER
# ==========================================================================


def _look(clf, yaw, pitch):
    return clf.decide(_obs(), GazeVector(math.radians(yaw), math.radians(pitch), 0.9))


def test_a_head_raised_or_turned_a_little_is_the_nearest_target_not_other():
    # Lens and screen centre a few degrees apart fold into one (as head-pose looks often do).
    merged, quality = ReferenceAnchorClassifier.fit(
        _three(screen_at=(0.8, -6.0), bottom=(0.6, -13.0)), CalibrationConfig())
    assert quality.ok and merged.classes == ("CAMERA", "BOTTOM", "OTHER")
    assert _look(merged, 0.5, 5.0).label == "CAMERA"  # chin up a little above the lens
    assert _look(merged, -11.5, -6.0).label == "CAMERA"  # turned toward the screen's side: screen = audience here
    assert _look(merged, 0.6, -17.0).label == "BOTTOM"  # a little below the script
    separate, _ = ReferenceAnchorClassifier.fit(_three(screen_at=(0.8, -10.5)), CalibrationConfig())
    assert _look(separate, -15.0, -10.5).label == "SCREEN"  # the screen's side


def test_a_clear_look_away_is_still_other_with_its_direction():
    clf, _ = ReferenceAnchorClassifier.fit(_three(screen_at=(0.8, -6.0), bottom=(0.6, -13.0)), CalibrationConfig())
    side = _look(clf, -24.0, -6.0)  # far toward the image left = the presenter's right
    assert side.label == "OTHER" and side.direction == "RIGHT"
    up = _look(clf, 0.5, 14.0)
    assert up.label == "OTHER" and up.direction == "UP"


def test_without_the_margin_and_the_width_a_small_step_outside_was_other():
    clf, _ = ReferenceAnchorClassifier.fit(_three(screen_at=(0.8, -6.0), bottom=(0.6, -13.0)),
                                           CalibrationConfig(other_margin_deg=0.0, screen_min_halfwidth_deg=0.0))
    assert _look(clf, 0.5, 5.0).label == "OTHER"
    assert _look(clf, -11.5, -6.0).label == "OTHER"
