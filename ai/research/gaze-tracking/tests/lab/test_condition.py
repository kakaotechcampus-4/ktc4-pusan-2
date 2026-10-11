"""Contracts of the live condition monitor and the adaptive blink gate.

Condition (``gaze_lab.runtime.condition``): every signal ramps from 1.0 at its
warn bound to ``fail_reliability`` at its fail bound; reliability is the
minimum; ``issues`` names the culprits; a lost or replaced face is severe and
zeroes reliability; emission follows changes, a reliability move or the
heartbeat.

Blink (``gaze_lab.eye.blink``): a frame is a blink below ``blink_ratio`` x
the user's own reference -- the smaller of the recent median and the script-cue
median -- never below the absolute floor, and only once armed.
"""

from __future__ import annotations

import math

import pytest

from gaze_lab.config import ConditionConfig, PreprocessConfig
from gaze_lab.eye.blink import AdaptiveBlinkGate
from gaze_lab.preprocess.headpose import focal_length_px
from gaze_lab.runtime.condition import (
    ConditionIssue,
    ConditionMonitor,
    SceneBaseline,
    SceneBaselineAccumulator,
    baseline_from_observations,
    head_jitter_deg,
    jitter_limits,
)
from gaze_lab.schemas import FaceScene, FrameObservation, FrameQuality, HeadPose

FRAME_MS = 125
SIZE = (640, 480)
BASE_BBOX = (250, 150, 140, 180)
#: The face-mesh distance that agrees with an 11 px iris at this frame size.
DEPTH_CM = focal_length_px(SIZE) * 1.17 / 11.0


def _obs(
    t_ms, *, bbox=BASE_BBOX, iris=11.0, yaw=0.0, pitch=-8.0, brightness=120.0, second=0.0,
    valid=True, reason=None, ear=0.3, depth=0.0,
):
    has_face = reason != "NO_FACE"
    # A head turning in place slides the face toward the turn (~8 cm in front of
    # the rotation centre), as a real camera sees it.
    px_per_cm = iris / 1.17
    a, b, b0 = math.radians(yaw), math.radians(pitch), math.radians(-8.0)
    bbox = (bbox[0] + 8.0 * math.sin(a) * math.cos(b) * px_per_cm,
            bbox[1] - 8.0 * (math.sin(b) - math.sin(b0)) * px_per_cm, bbox[2], bbox[3])
    return FrameObservation(
        frame_id=t_ms // FRAME_MS, t_ms=t_ms, face_confidence=1.0, face_valid=valid,
        invalid_reason=reason,
        head_pose=HeadPose(yaw=math.radians(yaw), pitch=math.radians(pitch),
                           depth_proxy=depth / focal_length_px(SIZE)),
        quality=FrameQuality(face_brightness=brightness, background_brightness=brightness,
                             left_eye_openness=ear, right_eye_openness=ear),
        face_bbox=bbox if has_face else None, image_size=SIZE,
        scene=FaceScene(n_faces=1 if has_face else 0, second_face_area_ratio=second,
                        iris_diameter_px=iris if has_face else 0.0),
    )


@pytest.fixture
def ccfg() -> ConditionConfig:
    return ConditionConfig()


@pytest.fixture
def baseline() -> SceneBaseline:
    return baseline_from_observations([_obs(i * FRAME_MS) for i in range(10)])


def _monitor(ccfg, baseline, n_good=8):
    monitor = ConditionMonitor(ccfg, baseline)
    for i in range(n_good):
        monitor.update(_obs(i * FRAME_MS))
    return monitor, n_good * FRAME_MS


def test_the_baseline_is_the_median_calibration_scene(baseline):
    assert baseline.centre == pytest.approx(((250 + 70) / 640, (150 + 90) / 480))
    assert baseline.iris_px == pytest.approx(11.0)
    assert baseline.head == pytest.approx((0.0, -8.0))
    assert SceneBaselineAccumulator().build() is None


def test_the_calibration_scene_itself_is_fully_reliable(ccfg, baseline):
    monitor, t = _monitor(ccfg, baseline)
    state = monitor.update(_obs(t))
    assert state.reliability == 1.0 and state.issues == [] and not state.severe


@pytest.mark.parametrize(
    "kwargs, issue, component",
    [
        ({"yaw": 20.0}, ConditionIssue.HEAD_TURNED, "head"),
        ({"iris": 5.0}, ConditionIssue.TOO_FAR, "distance"),
        ({"iris": 11.0 / 0.6}, ConditionIssue.TOO_CLOSE, "distance"),
        ({"bbox": (330, 150, 140, 180)}, ConditionIssue.OFF_CENTER, "position"),
    ],
)
def test_each_drift_lowers_reliability_and_names_itself(ccfg, baseline, kwargs, issue, component):
    monitor, t = _monitor(ccfg, baseline)
    state = monitor.update(_obs(t, **kwargs))
    assert issue.value in state.issues
    assert state.reliability < 1.0
    assert state.reliability == pytest.approx(min(state.components.values()))
    assert state.components[component] == pytest.approx(state.reliability)
    assert not state.severe


def test_reliability_ramps_linearly_between_warn_and_fail(ccfg, baseline):
    monitor, t = _monitor(ccfg, baseline)
    mid = (ccfg.head_warn_deg + ccfg.head_fail_deg) / 2
    state = monitor.update(_obs(t, yaw=mid))
    assert state.components["head"] == pytest.approx((1.0 + ccfg.fail_reliability) / 2)
    beyond = monitor.update(_obs(t + FRAME_MS, yaw=ccfg.head_fail_deg + 20))
    assert beyond.components["head"] == pytest.approx(ccfg.fail_reliability)


def test_a_run_of_invalid_frames_lowers_the_valid_ratio(ccfg, baseline):
    monitor, t = _monitor(ccfg, baseline)
    state = None
    for i in range(12):
        state = monitor.update(_obs(t + i * FRAME_MS, valid=False, reason="EYES_CLOSED"))
    assert ConditionIssue.LOW_VALID_RATIO.value in state.issues
    assert state.components["valid"] < 1.0


def test_a_face_gone_long_enough_is_severe(ccfg, baseline):
    monitor, t = _monitor(ccfg, baseline)
    short = monitor.update(_obs(t + 500, valid=False, reason="NO_FACE"))
    assert not short.severe
    lost = monitor.update(_obs(t + ccfg.face_lost_ms + 10, valid=False, reason="NO_FACE"))
    assert lost.severe and lost.reliability == 0.0
    assert lost.issues[0] == ConditionIssue.FACE_LOST.value
    back = monitor.update(_obs(t + ccfg.face_lost_ms + 200))
    assert not back.severe


def test_a_second_person_is_a_notice_once_seen_for_a_while_not_a_reliability_cut(ccfg, baseline):
    monitor, t = _monitor(ccfg, baseline)
    # A find that lasts a few frames is not a person.
    for i in range(4):
        assert monitor.update(_obs(t + i * FRAME_MS, second=0.8)).notices == []
    assert monitor.update(_obs(t + 4 * FRAME_MS)).notices == []
    start = t + 5 * FRAME_MS
    early = monitor.update(_obs(start, second=0.8))
    assert early.notices == []
    held = monitor.update(_obs(start + ccfg.second_face_confirm_ms, second=0.8))
    assert held.notices == [ConditionIssue.SECOND_FACE.value]
    # The presenter's face is still the one measured: reliability and the event are untouched.
    assert held.reliability == 1.0 and held.issues == [] and "second_face" not in held.components
    assert held.to_event().to_dict()["issues"] == []


def test_a_light_change_alone_is_not_a_measurement_problem(ccfg, baseline):
    monitor, t = _monitor(ccfg, baseline)
    state = None
    for i in range(8):
        state = monitor.update(_obs(t + i * FRAME_MS, brightness=40.0))  # a third of the calibration light
    assert state.reliability == 1.0 and state.issues == []


def test_head_jitter_is_the_frame_to_frame_scatter_not_the_movement():
    steady = [(i * FRAME_MS, 0.5, 11.0) for i in range(16)]
    assert head_jitter_deg(steady, 300, 6) == pytest.approx(0.0)
    turning = [(i * FRAME_MS, 0.5 + 2.0 * i, 11.0 - 1.0 * i) for i in range(16)]  # 16 deg/s, smooth
    assert head_jitter_deg(turning, 300, 6) == pytest.approx(0.0, abs=1e-9)
    noisy = [(i * FRAME_MS, 0.5 + (2.0 if i % 2 else -2.0), 11.0) for i in range(16)]
    # +-2 deg alternating: |d2| = 8, so 1.4826 x 8 / sqrt(6)
    assert head_jitter_deg(noisy, 300, 6) == pytest.approx(1.4826 * 8.0 / math.sqrt(6.0))
    assert head_jitter_deg(noisy[:7], 300, 6) is None  # five differences: too few
    gappy = [(i * 400, 0.5 + (2.0 if i % 2 else -2.0), 11.0) for i in range(16)]
    assert head_jitter_deg(gappy, 300, 6) is None  # frames too far apart to compare


def test_shaky_head_angles_lower_reliability_steady_ones_do_not(ccfg):
    acc = SceneBaselineAccumulator()
    for i, (cue, pitch) in enumerate([("CAMERA", 18.0), ("SCREEN", 11.0), ("BOTTOM", 4.0)] * 4):
        acc.add(_obs(i * FRAME_MS, pitch=pitch), cue=cue)
    base = acc.build()
    warn, fail = jitter_limits(base, ccfg)
    assert (warn, fail) == pytest.approx((1.75, 3.5))  # half of the 7 deg cue gap, and half of that
    monitor = ConditionMonitor(ccfg, base, head_is_gaze=True)
    state = None
    for i in range(16):
        state = monitor.update(_obs(i * FRAME_MS, pitch=11.0 + 0.2 * ((i % 3) - 1)))
    assert state.jitter_deg < warn and ConditionIssue.NOISY_TRACKING.value not in state.issues
    for i in range(16, 40):
        state = monitor.update(_obs(i * FRAME_MS, pitch=11.0 + (2.5 if i % 2 else -2.5)))
    assert ConditionIssue.NOISY_TRACKING.value in state.issues
    assert state.components["jitter"] < 1.0 and state.reliability == pytest.approx(min(state.components.values()))
    assert not state.severe


def test_moving_back_gradually_is_not_a_different_person(ccfg, baseline):
    # The face shrinks to a third of its calibrated area as the presenter leans
    # back -- a move (drift, face size), never a replacement.
    monitor, t = _monitor(ccfg, baseline)
    state = None
    for i in range(24):
        k = 1.0 - 0.42 * (i / 23.0)
        w, h = 140 * k, 180 * k
        bbox = (250 + 70 - w / 2, 150 + 90 - h / 2, w, h)
        state = monitor.update(_obs(t + i * FRAME_MS, bbox=bbox, iris=11.0 * k))
    assert ConditionIssue.FACE_REPLACED.value not in state.issues
    assert ConditionIssue.SECOND_FACE.value not in state.issues


def test_a_different_face_in_a_different_place_is_severe_once_confirmed(ccfg, baseline):
    monitor, t = _monitor(ccfg, baseline)
    elsewhere = dict(bbox=(20, 60, 300, 380))  # far left and much bigger
    first = monitor.update(_obs(t, **elsewhere))
    assert not first.severe
    confirmed = monitor.update(_obs(t + ccfg.replace_confirm_ms, **elsewhere))
    assert confirmed.severe
    assert confirmed.issues[0] == ConditionIssue.FACE_REPLACED.value
    # Back where the calibrated face was: it is the presenter again.
    back = monitor.update(_obs(t + ccfg.replace_confirm_ms + FRAME_MS))
    assert ConditionIssue.FACE_REPLACED.value not in back.issues


def test_a_jump_that_comes_straight_back_is_not_a_replacement(ccfg, baseline):
    monitor, t = _monitor(ccfg, baseline)
    monitor.update(_obs(t, bbox=(20, 60, 300, 380)))  # one frame on another detection
    for i in range(1, 8):
        state = monitor.update(_obs(t + i * FRAME_MS))
    assert not state.severe and ConditionIssue.FACE_REPLACED.value not in state.issues


def test_rebaseline_accepts_the_new_posture(ccfg, baseline):
    monitor, t = _monitor(ccfg, baseline)
    moved = dict(bbox=(330, 150, 140, 180))
    assert ConditionIssue.OFF_CENTER.value in monitor.update(_obs(t, **moved)).issues
    monitor.rebaseline(baseline_from_observations([_obs(0, **moved)]))
    assert monitor.update(_obs(t + FRAME_MS, **moved)).issues == []


def test_emission_follows_changes_and_the_heartbeat(ccfg, baseline):
    monitor = ConditionMonitor(ccfg, baseline)
    first = monitor.update(_obs(0))
    assert monitor.should_emit(first)
    assert not monitor.should_emit(monitor.update(_obs(FRAME_MS)))
    moved = (330, 150, 140, 180)
    changed = monitor.update(_obs(2 * FRAME_MS, bbox=moved))
    assert monitor.should_emit(changed)
    quiet = monitor.update(_obs(3 * FRAME_MS, bbox=moved))
    assert not monitor.should_emit(quiet)
    heartbeat = monitor.update(_obs(2 * FRAME_MS + ccfg.heartbeat_ms, bbox=moved))
    assert monitor.should_emit(heartbeat)


def test_the_event_is_the_four_key_contract(ccfg, baseline):
    monitor, t = _monitor(ccfg, baseline)
    event = monitor.update(_obs(t, yaw=25.0)).to_event()
    assert set(event.to_dict()) == {"type", "t_ms", "reliability", "issues"}
    assert event.to_dict()["issues"] == [ConditionIssue.HEAD_TURNED.value]


def test_a_face_near_the_recognition_floor_lowers_reliability(ccfg, baseline):
    monitor, t = _monitor(ccfg, baseline)
    # Moving far away: the face shrinks toward the size the detector drops (1 % of the frame).
    small = monitor.update(_obs(t, bbox=(300, 200, 60, 75), iris=4.5))
    assert ConditionIssue.TOO_FAR.value in small.issues and small.components["size"] < 1.0
    # Moving very close: the face fills most of the frame.
    big = monitor.update(_obs(t + FRAME_MS, bbox=(150, 20, 330, 420), iris=26.0))
    assert ConditionIssue.TOO_CLOSE.value in big.issues and big.components["size"] < 1.0
    # An ordinary size is no problem at all.
    assert monitor.update(_obs(t + 2 * FRAME_MS)).components["size"] == 1.0


# ==========================================================================
# position drift: how far the head moved from the calibration position
# ==========================================================================


def _cue_baseline(camera=(0.0, 18.0), screen=(0.0, 11.0), bottom=(0.0, 4.0)):
    acc = SceneBaselineAccumulator()
    for cue, (yaw, pitch) in (("CAMERA", camera), ("SCREEN", screen), ("BOTTOM", bottom)):
        for i in range(4):
            acc.add(_obs(i * FRAME_MS, yaw=yaw, pitch=pitch), cue=cue)
    return acc.build()


def test_the_limits_follow_the_calibrated_cue_separation(ccfg):
    from gaze_lab.runtime.condition import drift_limits

    warn, fail, span = drift_limits(_cue_baseline(), ccfg)
    assert fail == pytest.approx(7.0) and warn == pytest.approx(4.2)  # the 7 deg gap, warn at 60 %
    assert span == pytest.approx(7.0)
    # Lens and screen on one posture: the tightest gap is tiny, the floor holds.
    assert drift_limits(_cue_baseline(screen=(0.0, 17.5)), ccfg)[1] == pytest.approx(ccfg.drift_fail_min_deg)
    # Far-apart postures: the ceiling holds.
    assert drift_limits(_cue_baseline(camera=(0.0, 40.0), screen=(0.0, 10.0), bottom=(0.0, -20.0)), ccfg)[1] == pytest.approx(
        ccfg.drift_fail_max_deg)


@pytest.mark.parametrize("depth", [0.0, DEPTH_CM], ids=["iris", "face-mesh"])
def test_a_head_turned_in_place_is_not_a_move(ccfg, depth):
    base = baseline_from_observations([_obs(i * FRAME_MS, depth=depth) for i in range(10)])
    monitor = ConditionMonitor(ccfg, base, head_is_gaze=True)
    for i in range(6):
        state = monitor.update(_obs(i * FRAME_MS, yaw=35.0, pitch=10.0, depth=depth))
    assert state.drift["position_deg"] == pytest.approx(0.0, abs=1e-6)
    assert state.reliability == 1.0 and state.issues == []


def test_hidden_eyes_do_not_read_as_a_move(ccfg):
    # Head lowered or turned aside: the iris shrinks or is guessed, but the
    # face-mesh distance -- what the drift reads when calibrated with it --
    # stays where it was.
    base = baseline_from_observations([_obs(i * FRAME_MS, depth=DEPTH_CM) for i in range(10)])
    assert base.depth_cm == pytest.approx(DEPTH_CM)
    monitor = ConditionMonitor(ccfg, base, head_is_gaze=True)
    for i in range(6):
        state = monitor.update(_obs(i * FRAME_MS, iris=6.0, depth=DEPTH_CM))
    assert state.drift["distance_cm"] == pytest.approx(DEPTH_CM)
    assert state.drift["distance_deg"] == pytest.approx(0.0, abs=1e-9)
    assert state.reliability == 1.0 and state.issues == []


def test_moving_away_is_read_from_the_face_mesh(ccfg):
    base = baseline_from_observations([_obs(i * FRAME_MS, depth=DEPTH_CM) for i in range(10)])
    monitor = ConditionMonitor(ccfg, base)
    state = monitor.update(_obs(0, depth=DEPTH_CM / 0.3))  # iris unchanged: the mesh decides
    span = ccfg.drift_default_span_deg
    expected = span - math.degrees(math.atan(math.tan(math.radians(span)) * 0.3))
    assert state.drift["distance_deg"] == pytest.approx(expected)
    assert state.drift["closer_cm"] == pytest.approx(DEPTH_CM - DEPTH_CM / 0.3)
    assert ConditionIssue.TOO_FAR.value in state.issues


def test_without_the_face_mesh_distance_both_sides_use_the_iris(ccfg):
    # Never one source now and the other at calibration: the gap between the two
    # estimates would read as a move.
    base = baseline_from_observations([_obs(i * FRAME_MS, depth=DEPTH_CM * 1.2) for i in range(10)])
    monitor = ConditionMonitor(ccfg, base)
    drift = monitor.update(_obs(0, depth=0.0)).drift
    focal = focal_length_px(SIZE)
    assert drift["distance_cm"] == pytest.approx(focal * 1.17 / 11.0)
    assert drift["calibrated_distance_cm"] == pytest.approx(focal * 1.17 / 11.0)


def test_a_move_is_reported_in_cm_and_degrees_from_the_presenters_side(ccfg, baseline):
    monitor, t = _monitor(ccfg, baseline)
    moved = (BASE_BBOX[0] + 40, BASE_BBOX[1], BASE_BBOX[2], BASE_BBOX[3])  # 40 px toward image right
    drift = monitor.update(_obs(t, bbox=moved)).drift
    cm = 40 * 1.17 / 11.0
    assert drift["right_cm"] == pytest.approx(-cm)  # image right is the presenter's LEFT
    assert drift["up_cm"] == pytest.approx(0.0, abs=1e-9)
    assert drift["position_deg"] == pytest.approx(math.degrees(math.atan2(cm, drift["distance_cm"])))
    assert drift["closer_cm"] == pytest.approx(0.0, abs=1e-9)


def test_moving_closer_is_read_as_the_change_of_the_farthest_cue_angle(ccfg, baseline):
    monitor, t = _monitor(ccfg, baseline)
    state = monitor.update(_obs(t, iris=11.0 / 0.6))  # 40 % closer
    span = ccfg.drift_default_span_deg
    expected = math.degrees(math.atan(math.tan(math.radians(span)) / 0.6)) - span
    assert state.drift["distance_deg"] == pytest.approx(expected)
    assert state.drift["closer_cm"] > 0
    assert ConditionIssue.TOO_CLOSE.value in state.issues


def test_moving_too_far_for_long_enough_makes_the_measurement_unusable(ccfg, baseline):
    monitor, t = _monitor(ccfg, baseline)
    far = (BASE_BBOX[0] - 70, BASE_BBOX[1], BASE_BBOX[2], BASE_BBOX[3])
    first = monitor.update(_obs(t, bbox=far))
    assert not first.severe and ConditionIssue.OFF_CENTER.value in first.issues
    held = monitor.update(_obs(t + ccfg.drift_confirm_ms, bbox=far))
    assert held.severe and held.reliability == 0.0
    assert held.issues[0] == ConditionIssue.MOVED_TOO_FAR.value
    back = monitor.update(_obs(t + ccfg.drift_confirm_ms + FRAME_MS))
    assert not back.severe and back.reliability == 1.0


# ==========================================================================
# adaptive blink gate
# ==========================================================================


@pytest.fixture
def pre() -> PreprocessConfig:
    return PreprocessConfig()


def test_the_gate_is_off_until_calibrated(pre):
    gate = AdaptiveBlinkGate(pre)
    obs = _obs(0, ear=0.13)
    assert gate.apply(obs) is False and obs.face_valid


def test_a_blink_against_the_users_own_eye_is_caught(pre):
    gate = AdaptiveBlinkGate(pre)
    gate.calibrate([0.32] * 10, [])
    obs = _obs(0, ear=0.18)  # well above the absolute 0.12 floor
    assert gate.apply(obs) is True
    assert not obs.face_valid and obs.invalid_reason == "EYES_CLOSED"


def test_reading_the_script_is_not_a_blink(pre):
    """The lid follows the eye down; the script-cue reference keeps those frames."""
    gate = AdaptiveBlinkGate(pre)
    gate.calibrate([0.32] * 10, [0.21] * 10)
    reading = _obs(0, ear=0.19)
    assert gate.apply(reading) is False and reading.face_valid
    assert gate.threshold() == pytest.approx(max(pre.min_eye_openness, pre.blink_ratio * 0.21))


def test_the_threshold_never_drops_below_the_absolute_floor(pre):
    gate = AdaptiveBlinkGate(pre)
    gate.calibrate([0.1] * 10, [0.1] * 10)
    assert gate.threshold() == pytest.approx(pre.min_eye_openness)


def test_a_gate_without_enough_reference_stays_quiet(pre):
    gate = AdaptiveBlinkGate(pre)
    gate.calibrate([0.3] * (pre.blink_min_reference_frames - 1), [])
    assert gate.threshold() is None
    gate.calibrate([0.0] * 20, [])  # an all-zero stub never arms it
    assert gate.threshold() is None


def test_disabled_adaptive_blink_never_fires(pre):
    pre.adaptive_blink = False
    gate = AdaptiveBlinkGate(pre)
    gate.calibrate([0.32] * 10, [])
    assert gate.apply(_obs(0, ear=0.15)) is False
