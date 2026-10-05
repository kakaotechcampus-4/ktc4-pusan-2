"""Contracts of the set-up check (``vision.runtime.preconditions``).

PASS needs every check to hold for ``hold_ms``; REJECT needs one check to fail
for ``reject_after_ms``; everything else is RETRY.  Each test drives one check
with a hand-built observation (640x480, 8 FPS) and names the reason it expects.
"""

from __future__ import annotations

import math

import pytest

from vision.config import PreconditionConfig
from vision.runtime.preconditions import (
    PreconditionChecker,
    PreconditionReason,
    PreconditionStatus,
    head_distance_cm,
    iris_distance_cm,
)
from vision.preprocess.headpose import focal_length_px
from vision.schemas import FaceScene, FrameObservation, FrameQuality, HeadPose

FRAME_MS = 125
SIZE = (640, 480)


def _obs(
    t_ms,
    *,
    bbox=(250, 150, 140, 180),
    iris=11.0,
    second=0.0,
    yaw=0.0,
    pitch=-8.0,
    brightness=120.0,
    background=100.0,
    valid=True,
    reason=None,
    depth=0.0,
):
    has_face = reason != "NO_FACE"
    return FrameObservation(
        frame_id=t_ms // FRAME_MS, t_ms=t_ms, face_confidence=1.0, face_valid=valid,
        invalid_reason=reason,
        head_pose=HeadPose(yaw=math.radians(yaw), pitch=math.radians(pitch),
                           depth_proxy=depth / focal_length_px(SIZE)),
        quality=FrameQuality(face_brightness=brightness, background_brightness=background,
                             left_eye_openness=0.3, right_eye_openness=0.3),
        face_bbox=bbox if has_face else None,
        image_size=SIZE,
        scene=FaceScene(n_faces=1 if has_face else 0, second_face_area_ratio=second,
                        iris_diameter_px=iris if has_face else 0.0),
    )


def _run(checker, start_ms, n_frames, **kwargs):
    report = None
    for i in range(n_frames):
        report = checker.update(_obs(start_ms + i * FRAME_MS, **kwargs))
    return report


@pytest.fixture
def pcfg() -> PreconditionConfig:
    return PreconditionConfig()


def test_a_good_set_up_passes_after_the_hold(pcfg):
    checker = PreconditionChecker(pcfg)
    early = _run(checker, 0, 4)
    assert early.status == PreconditionStatus.RETRY.value and early.reason == "OK"
    late = _run(checker, 4 * FRAME_MS, 6)
    assert late.status == PreconditionStatus.PASS.value
    assert late.held_ms >= pcfg.hold_ms
    assert all(check.ok for check in late.checks)
    assert late.measurements["distance_cm"] == pytest.approx(iris_distance_cm(11.0, SIZE), abs=0.1)


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"reason": "NO_FACE", "valid": False}, PreconditionReason.NO_FACE),
        ({"bbox": (20, 150, 140, 180)}, PreconditionReason.OFF_CENTER),
        ({"reason": "OUT_OF_FRAME", "valid": False}, PreconditionReason.OFF_CENTER),
        ({"bbox": (300, 210, 50, 60), "iris": 4.0}, PreconditionReason.TOO_FAR),  # ~1 % of the frame
        ({"reason": "FACE_TOO_SMALL", "valid": False, "iris": 0.0}, PreconditionReason.TOO_FAR),
        ({"bbox": (190, 50, 260, 350)}, PreconditionReason.TOO_CLOSE),  # 73 % of the height
        ({"bbox": (170, 0, 300, 470)}, PreconditionReason.TOO_CLOSE),
        ({"yaw": 35.0}, PreconditionReason.FACING_AWAY),
        ({"brightness": 30.0, "background": 30.0}, PreconditionReason.TOO_DARK),
        ({"brightness": 80.0, "background": 230.0}, PreconditionReason.BACKLIT),
    ],
)
def test_each_failing_check_retries_then_rejects_with_its_own_reason(pcfg, kwargs, reason):
    checker = PreconditionChecker(pcfg)
    first = _run(checker, 0, 4, **kwargs)
    assert first.status == PreconditionStatus.RETRY.value
    assert first.reason == reason.value
    assert first.hint
    rejected = _run(checker, 4 * FRAME_MS, 30, **kwargs)
    assert rejected.status == PreconditionStatus.REJECT.value
    assert rejected.reason == reason.value
    assert rejected.failing_ms >= pcfg.reject_after_ms
    assert rejected.blocking is True


def test_the_bounds_are_what_tracking_needs_not_an_ideal_posture(pcfg):
    checker = PreconditionChecker(pcfg)
    # Far (a small iris, ~70 cm at 480 px), a little off-centre and dim: still measurable.
    report = _run(checker, 0, 12, bbox=(140, 170, 110, 140), iris=6.5, brightness=55.0, background=90.0)
    assert report.status == PreconditionStatus.PASS.value, report


def test_only_an_eye_backbone_needs_the_iris_floor(pcfg):
    small_iris = dict(iris=5.0)
    assert _run(PreconditionChecker(pcfg), 0, 12, **small_iris).status == PreconditionStatus.PASS.value
    eye = _run(PreconditionChecker(pcfg, eye_based=True), 0, 12, **small_iris)
    assert eye.reason == PreconditionReason.TOO_FAR.value


def test_a_second_person_fails_the_check_once_seen_for_a_while(pcfg):
    checker = PreconditionChecker(pcfg)
    # A false find for a few frames does not stop the check from passing.
    flicker = _run(checker, 0, 3, second=0.6)
    assert flicker.reason == PreconditionReason.OK.value
    assert _run(checker, 3 * FRAME_MS, 10).status == PreconditionStatus.PASS.value
    checker = PreconditionChecker(pcfg)
    early = _run(checker, 0, 4, second=0.6)  # 375 ms
    assert early.reason == PreconditionReason.OK.value
    seen = _run(checker, 4 * FRAME_MS, 2, second=0.6)
    assert seen.status == PreconditionStatus.RETRY.value
    assert seen.reason == PreconditionReason.MULTIPLE_FACES.value
    rejected = _run(checker, 6 * FRAME_MS, 30, second=0.6)
    assert rejected.status == PreconditionStatus.REJECT.value
    assert rejected.reason == PreconditionReason.MULTIPLE_FACES.value


def test_advisory_mode_rejects_without_blocking():
    checker = PreconditionChecker(PreconditionConfig(strict=False))
    report = _run(checker, 0, 40, second=0.8)
    assert report.status == PreconditionStatus.REJECT.value
    assert report.blocking is False


def test_a_slow_stream_is_low_fps(pcfg):
    checker = PreconditionChecker(pcfg)
    report = None
    for i in range(40):
        report = checker.update(_obs(i * 400))  # 2.5 analysed frames per second
    assert report.reason == PreconditionReason.LOW_FPS.value
    assert report.status == PreconditionStatus.REJECT.value


def test_a_blink_neither_breaks_nor_starts_the_hold(pcfg):
    checker = PreconditionChecker(pcfg)
    _run(checker, 0, 6)
    held = checker.last_report.held_ms
    blink = checker.update(_obs(6 * FRAME_MS, valid=False, reason="EYES_CLOSED"))
    assert blink.reason == "OK" and blink.held_ms >= held
    assert _run(checker, 7 * FRAME_MS, 2).status == PreconditionStatus.PASS.value


def test_one_bad_frame_restarts_the_hold(pcfg):
    checker = PreconditionChecker(pcfg)
    _run(checker, 0, 7)
    checker.update(_obs(7 * FRAME_MS, bbox=(20, 150, 140, 180)))
    assert _run(checker, 8 * FRAME_MS, 2).status == PreconditionStatus.RETRY.value
    assert _run(checker, 10 * FRAME_MS, 8).status == PreconditionStatus.PASS.value


def test_the_longest_failing_check_is_the_one_reported(pcfg):
    checker = PreconditionChecker(pcfg)
    _run(checker, 0, 4, yaw=35.0)  # facing away first
    report = _run(checker, 4 * FRAME_MS, 2, yaw=35.0, brightness=20.0, background=20.0)
    assert report.reason == PreconditionReason.FACING_AWAY.value


def test_iris_distance_follows_the_pinhole_model():
    # 63 deg vertical FOV at 480 px: f = 240 / tan(31.5 deg) ~ 391.7 px.
    focal = 240.0 / math.tan(math.radians(31.5))
    assert iris_distance_cm(10.0, SIZE) == pytest.approx(focal * 1.17 / 10.0)
    assert math.isnan(iris_distance_cm(0.0, SIZE))


def test_the_distance_shown_comes_from_the_face_mesh_when_there_is_one(pcfg):
    assert head_distance_cm(HeadPose(depth_proxy=0.15), SIZE) == pytest.approx(0.15 * focal_length_px(SIZE))
    assert math.isnan(head_distance_cm(HeadPose(depth_proxy=math.nan), SIZE))
    assert math.isnan(head_distance_cm(HeadPose(), SIZE))
    checker = PreconditionChecker(pcfg)
    # Eyes hidden (iris guessed small): the face-mesh distance is what is shown.
    report = _run(checker, 0, 2, iris=5.0, depth=58.0)
    assert report.measurements["distance_cm"] == pytest.approx(58.0)
    assert report.measurements["iris_px"] == pytest.approx(5.0)


def test_reset_forgets_everything(pcfg):
    checker = PreconditionChecker(pcfg)
    _run(checker, 0, 40, second=0.9)
    checker.reset()
    assert checker.last_report is None
    assert _run(checker, 0, 2).status == PreconditionStatus.RETRY.value
