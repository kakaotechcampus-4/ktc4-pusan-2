"""The judgement criteria, one scenario at a time, through the whole session.

Each test is one row of the scenario table in the v1 README (§7-13): a
presenter does something in front of the camera and the session -- preprocess
stand-in -> head-pose backbone -> reference classifier -> condition monitor --
must answer what the table says.  The stand-in pipeline reports what the real
one would for that picture: the face box slides with a turned head and scales
with distance, the iris and the face-mesh distance follow, closed eyes are
EYES_CLOSED, and the second-face signal comes from the real
``second_face_ratio`` over the boxes in the picture (so a duplicate find or a
tiny background find is judged by the same rule as in production).

Head angles are raw-frame degrees (yaw > 0 turns toward the image right, i.e.
the presenter's LEFT); directions are reported presenter-centric.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np
import pytest

from gaze_lab.backbones.head import HeadPoseBackbone
from gaze_lab.preprocess.headpose import focal_length_px
from gaze_lab.preprocess.pipeline import second_face_ratio
from gaze_lab.runtime.condition import ConditionIssue
from gaze_lab.runtime.session import VisionSession
from gaze_lab.schemas import FaceScene, FrameObservation, FrameQuality, HeadPose, InvalidReason

FRAME_MS = 125
FRAME = np.zeros((4, 4, 3), dtype=np.uint8)
SIZE = (640, 480)
#: The calibrated face: box, iris and the face-mesh distance that agrees with it.
BOX = (250.0, 150.0, 140.0, 180.0)
IRIS_PX = 11.0
DEPTH_CM = focal_length_px(SIZE) * 1.17 / IRIS_PX
PX_PER_CM = IRIS_PX / 1.17

#: Head postures (yaw, pitch) of the three looks on a laptop: the lens is above eye level.
LENS, SCREEN, SCRIPT = (0.5, 18.0), (0.8, 11.0), (0.6, 4.0)


class Presenter:
    """What the camera sees, steered by the test; reports what PreprocessPipeline would."""

    def __init__(self) -> None:
        self.yaw, self.pitch = SCREEN
        #: Sideways / up-down move of the head from the calibration seat, cm (image axes).
        self.moved_cm = (0.0, 0.0)
        #: Distance factor: 1 at the calibration distance, 0.5 = twice as far.
        self.scale = 1.0
        self.eyes_closed = False
        self.present = True
        #: Face luminance (0-255).
        self.brightness = 120.0
        #: Shaky landmarks: the measured head angles jump +-this many degrees frame to frame.
        self.jitter = 0.0
        #: Other boxes MediaPipe returned in the same picture.
        self.others: List[Tuple[float, float, float, float]] = []
        #: The main face is somewhere else entirely (another person took the seat).
        self.box_override: Optional[Tuple[float, float, float, float]] = None

    def box(self) -> Tuple[float, float, float, float]:
        if self.box_override is not None:
            return self.box_override
        k = self.scale
        cx, cy = BOX[0] + BOX[2] / 2, BOX[1] + BOX[3] / 2
        ppc = PX_PER_CM * k
        a, b = math.radians(self.yaw), math.radians(self.pitch)
        # The face sits ~8 cm in front of the head's rotation centre: a turn slides it.
        cx += (8.0 * math.sin(a) * math.cos(b) + self.moved_cm[0]) * ppc
        cy += (-8.0 * math.sin(b) + self.moved_cm[1]) * ppc
        w, h = BOX[2] * k, BOX[3] * k
        return (cx - w / 2, cy - h / 2, w, h)

    def process_bgr(self, bgr, frame_id, t_ms, *, main_face_hint=None) -> FrameObservation:
        if not self.present:
            return FrameObservation(frame_id=int(frame_id), t_ms=int(t_ms), face_confidence=0.0, face_valid=False,
                                    invalid_reason=InvalidReason.NO_FACE.value, image_size=SIZE,
                                    scene=FaceScene(n_faces=0))
        box = self.box()
        reason = InvalidReason.EYES_CLOSED.value if self.eyes_closed else None
        shake = self.jitter if int(frame_id) % 2 else -self.jitter
        return FrameObservation(
            frame_id=int(frame_id), t_ms=int(t_ms), face_confidence=1.0, face_valid=reason is None,
            invalid_reason=reason,
            head_pose=HeadPose(yaw=math.radians(self.yaw + shake), pitch=math.radians(self.pitch + shake),
                               depth_proxy=DEPTH_CM / self.scale / focal_length_px(SIZE)),
            quality=FrameQuality(face_brightness=self.brightness, background_brightness=110.0,
                                 left_eye_openness=0.05 if self.eyes_closed else 0.3,
                                 right_eye_openness=0.05 if self.eyes_closed else 0.3),
            face_bbox=box, image_size=SIZE,
            scene=FaceScene(n_faces=1 + len(self.others),
                            second_face_area_ratio=second_face_ratio(box, self.others, SIZE, 0.010),
                            iris_diameter_px=IRIS_PX * self.scale),
        )

    def close(self) -> None:
        pass


class Take:
    """A calibrated session and its clock."""

    def __init__(self, cfg, looks=(LENS, SCREEN, SCRIPT), circle_at: Optional[Tuple[float, float]] = None) -> None:
        self.presenter = Presenter()
        self.session = VisionSession(cfg, backbone=HeadPoseBackbone(), pipeline=self.presenter)
        self.t = 0
        #: Good frames each calibration look took.
        self.goods: dict = {}
        if circle_at is not None:
            # The head circle's first second: the presenter looks at the screen.
            self.presenter.yaw, self.presenter.pitch = circle_at
            self.session.start_head_sweep(self.t)
            for _ in range(cfg.sweep.neutral_frames):
                self.t += FRAME_MS
                self.session.offer_sweep_frame(FRAME, self.t)
        lens, screen, script = looks
        # The product's order: screen centre first (the direction reference), then lens, then script.
        for cue, (yaw, pitch) in (("SCREEN", screen), ("CAMERA", lens), ("BOTTOM", script)):
            self.session.start_calibration_cue(cue, self.t)
            status = None
            for i in range(80):
                jitter = ((i % 5) - 2) * 0.3
                self.presenter.yaw, self.presenter.pitch = yaw + jitter, pitch + jitter
                status = self.session.offer_calibration_frame(FRAME, self.t)
                self.t += FRAME_MS
                if status.finished:
                    break
            assert status.state == "DONE", (cue, status)
            self.goods[cue] = status.good
        quality = self.session.finish_calibration()
        assert quality.ok, quality
        self.presenter.yaw, self.presenter.pitch = screen

    def look(self, yaw: float, pitch: float, frames: int = 4):
        """Hold a head posture for a few frames; the last frame's decision."""
        self.presenter.yaw, self.presenter.pitch = yaw, pitch
        return self.run(frames)

    def run(self, frames: int):
        decision = None
        for _ in range(frames):
            _event, decision = self.session.process_frame(FRAME, self.t)
            self.t += FRAME_MS
        return decision

    @property
    def condition(self):
        return self.session.last_condition

    def close(self) -> None:
        self.session.close()


@pytest.fixture
def take(fresh_cfg):
    t = Take(fresh_cfg)
    yield t
    t.close()


# ==========================================================================
# Where the presenter looks
# ==========================================================================


def test_s01_looking_at_the_lens_is_eye_contact(take):
    d = take.look(*LENS)
    assert d.label == "CAMERA"


def test_s02_looking_at_the_screen_centre_is_the_screen(take):
    d = take.look(*SCREEN)
    assert d.label == "SCREEN"
    assert d.aim_deg == pytest.approx((0.0, 0.0), abs=1e-9)


def test_s03_reading_the_script_with_lowered_lids_is_the_script(take):
    take.presenter.eyes_closed = True  # lids down below the eyes-closed floor
    d = take.look(*SCRIPT)
    assert d.face_valid and d.label == "BOTTOM"


def test_s04_the_screens_side_is_still_the_screen_and_says_how_far(take):
    right = take.look(SCREEN[0] - 8.0, SCREEN[1])  # head 8 deg toward the presenter's right
    assert right.label == "SCREEN"
    assert right.aim_deg == pytest.approx((8.0, 0.0))
    left = take.look(SCREEN[0] + 8.0, SCREEN[1])
    assert left.label == "SCREEN" and left.aim_deg == pytest.approx((-8.0, 0.0))


@pytest.mark.parametrize("turn, direction", [(-25.0, "RIGHT"), (25.0, "LEFT")])
def test_s05_a_clear_look_to_the_side_is_elsewhere_with_its_side(take, turn, direction):
    d = take.look(SCREEN[0] + turn, SCREEN[1])
    assert d.label == "OTHER" and d.direction == direction
    assert take.condition.reliability == 1.0  # a direction, not a worse measurement


def test_s06_looking_well_above_the_lens_is_elsewhere_up(take):
    d = take.look(LENS[0], LENS[1] + 12.0)
    assert d.label == "OTHER" and d.direction == "UP"


def test_s07_a_chin_a_little_above_the_lens_is_still_the_lens(take):
    d = take.look(LENS[0], LENS[1] + 3.0)
    assert d.label == "CAMERA"


def test_s08_looking_far_below_the_script_is_elsewhere_down(take):
    d = take.look(SCRIPT[0], SCRIPT[1] - 15.0)
    assert d.label == "OTHER" and d.direction == "DOWN"


def test_s09_a_head_turned_far_with_the_eyes_out_of_sight_is_still_judged(take):
    take.presenter.eyes_closed = True
    d = take.look(SCREEN[0] + 40.0, SCREEN[1])
    assert d.face_valid and d.label == "OTHER" and d.direction == "LEFT"


def test_s10_between_the_screen_and_the_script_is_never_elsewhere(take):
    d = take.look(SCRIPT[0], (SCREEN[1] + SCRIPT[1]) / 2)
    assert d.label in ("SCREEN", "BOTTOM", "UNCERTAIN")
    assert d.label != "OTHER"


def test_s11_a_presenter_who_lifts_the_head_far_for_the_lens_still_gets_left_and_right(fresh_cfg):
    # 15 deg from the lens to the screen centre: the aspect alone would make the
    # screen ~27 deg wide each side and a clear turn aside would read as the screen.
    t = Take(fresh_cfg, looks=((0.5, 26.0), (0.8, 11.0), (0.6, 3.0)))
    try:
        d = t.look(0.8 - 22.0, 11.0)
        assert d.label == "OTHER" and d.direction == "RIGHT"
        assert t.look(0.8 - 8.0, 11.0).label == "SCREEN"
    finally:
        t.close()


def test_s20_a_blink_at_the_lens_does_not_interrupt_the_judgement(take):
    take.look(*LENS)
    take.presenter.eyes_closed = True
    d = take.look(*LENS, frames=2)
    assert d.face_valid and d.label == "CAMERA"


# ==========================================================================
# Who is in the picture
# ==========================================================================


def test_s12_leaning_back_alone_is_never_another_person(take):
    take.look(*SCREEN)
    for i in range(24):
        take.presenter.scale = 1.0 - 0.4 * i / 23.0  # to 60 % of the size: 36 % of the area
        take.run(1)
    assert take.condition.notices == []
    assert ConditionIssue.FACE_REPLACED.value not in take.condition.issues
    assert not take.condition.severe


def test_s13_the_same_face_found_twice_is_not_another_person(take):
    x, y, w, h = take.presenter.box()
    take.presenter.others = [(x + 6, y + 4, w * 0.9, h * 0.9)]
    take.look(*SCREEN, frames=16)
    assert take.condition.notices == []


def test_s14_a_tiny_find_in_the_background_is_not_another_person(take):
    take.presenter.scale = 0.6  # leaning back makes any background find large next to the face
    take.presenter.others = [(20, 20, 40, 50)]  # 0.65 % of the frame
    take.look(*SCREEN, frames=16)
    assert take.condition.notices == []


def test_s15_someone_passing_for_a_moment_is_ignored(take):
    take.presenter.others = [(480, 140, 120, 160)]
    take.look(*SCREEN, frames=3)
    take.presenter.others = []
    take.look(*SCREEN, frames=8)
    assert take.condition.notices == []


def test_s16_a_second_person_staying_is_a_notice_and_the_judgement_goes_on(take):
    take.presenter.others = [(480, 140, 120, 160)]
    early = take.look(*LENS, frames=4)
    assert take.condition.notices == []
    d = take.look(*LENS, frames=6)
    assert take.condition.notices == [ConditionIssue.SECOND_FACE.value]
    assert take.condition.reliability == 1.0 and take.condition.issues == []
    assert early.label == d.label == "CAMERA"


def test_s17_another_person_in_the_seat_stops_the_judgement(take):
    take.look(*SCREEN)
    take.presenter.box_override = (430.0, 60.0, 180.0, 230.0)  # a different face, elsewhere
    first = take.run(1)
    assert first.label != "UNCERTAIN" or first.uncertain_reason != ConditionIssue.FACE_REPLACED.value
    d = take.run(4)
    assert take.condition.severe and take.condition.issues[0] == ConditionIssue.FACE_REPLACED.value
    assert d.label == "UNCERTAIN" and d.uncertain_reason == ConditionIssue.FACE_REPLACED.value


def test_s17b_someone_else_sitting_down_right_after_calibration_is_caught(take):
    # No live frame of the presenter yet: the first one is compared with the calibrated face.
    take.presenter.box_override = (430.0, 60.0, 180.0, 230.0)
    take.run(5)
    assert take.condition.severe and take.condition.issues[0] == ConditionIssue.FACE_REPLACED.value


# ==========================================================================
# Measurement problems
# ==========================================================================


def test_s18_moving_seat_far_for_two_seconds_makes_the_measurement_unusable(take):
    take.look(*SCREEN)
    take.presenter.moved_cm = (12.0, 0.0)
    d = take.look(*SCREEN, frames=20)
    assert take.condition.severe and take.condition.issues[0] == ConditionIssue.MOVED_TOO_FAR.value
    assert d.label == "UNCERTAIN" and d.uncertain_reason == ConditionIssue.MOVED_TOO_FAR.value


def test_s21_a_light_change_alone_does_not_lower_reliability(take):
    take.look(*LENS)
    take.presenter.brightness = 45.0  # the room light dims to under half
    d = take.look(*LENS, frames=8)
    assert take.condition.reliability == 1.0 and take.condition.issues == []
    assert d.label == "CAMERA"


def test_s22_shaky_head_angles_lower_reliability(take):
    take.look(*LENS, frames=16)
    assert take.condition.jitter_deg < 0.5
    take.presenter.jitter = 3.0  # landmarks shaking +-3 deg frame to frame (dim light, blur)
    take.look(*LENS, frames=16)
    assert ConditionIssue.NOISY_TRACKING.value in take.condition.issues
    assert take.condition.reliability < 1.0 and not take.condition.severe


def test_s19_a_face_gone_for_a_second_and_a_half_is_unmeasurable(take, fresh_cfg):
    take.look(*SCREEN)
    take.presenter.present = False
    short = take.run(4)
    assert not take.condition.severe and short.uncertain_reason == InvalidReason.NO_FACE.value
    take.run(int(fresh_cfg.condition.face_lost_ms / FRAME_MS))
    assert take.condition.severe and take.condition.issues[0] == ConditionIssue.FACE_LOST.value
    take.presenter.present = True
    assert take.look(*SCREEN).label == "SCREEN"


# ==========================================================================
# The head circle measures the baseline; the calibration confirms and completes it
# ==========================================================================


def test_s23_the_screen_look_confirms_the_head_circle_and_is_short(fresh_cfg):
    t = Take(fresh_cfg, circle_at=SCREEN)
    try:
        check = t.session.baseline_check
        assert check.confirmed and check.seeded == fresh_cfg.sweep.neutral_frames
        assert t.goods["SCREEN"] == fresh_cfg.calibration.cue_confirm_frames  # 8 frames, not 16
        assert t.look(*LENS).label == "CAMERA" and t.look(*SCRIPT).label == "BOTTOM"
    finally:
        t.close()


def test_s24_a_presenter_who_moved_after_the_circle_is_measured_again(fresh_cfg):
    # A cushion after the head circle: every posture 5 deg higher.  The script look is then
    # only 2 deg below the circle's centre -- read from the circle, it could never fill.
    up = lambda pose: (pose[0], pose[1] + 5.0)  # noqa: E731
    t = Take(fresh_cfg, looks=(up(LENS), up(SCREEN), up(SCRIPT)), circle_at=SCREEN)
    try:
        check = t.session.baseline_check
        assert not check.confirmed and check.shift_deg == pytest.approx(5.0, abs=0.5)
        assert t.goods["SCREEN"] == fresh_cfg.calibration.target_good_frames
        assert t.look(*up(LENS)).label == "CAMERA" and t.look(*up(SCRIPT)).label == "BOTTOM"
    finally:
        t.close()


# ==========================================================================
# The set-up check, same people
# ==========================================================================


def _check(session, presenter, frames: int, t0: int):
    report = None
    for i in range(frames):
        report = session.check_preconditions(FRAME, t0 + i * FRAME_MS)
    return report


def test_p1_far_but_findable_passes_and_a_duplicate_find_is_one_person(fresh_cfg):
    presenter = Presenter()
    session = VisionSession(fresh_cfg, backbone=HeadPoseBackbone(), pipeline=presenter)
    try:
        presenter.scale = 0.5  # twice as far: the face is ~2 % of the frame
        x, y, w, h = presenter.box()
        presenter.others = [(x + 3, y + 2, w, h)]
        report = _check(session, presenter, 12, 0)
        assert report.status == "PASS", report
    finally:
        session.close()


def test_p2_a_second_person_fails_the_set_up_once_seen_for_half_a_second(fresh_cfg):
    presenter = Presenter()
    session = VisionSession(fresh_cfg, backbone=HeadPoseBackbone(), pipeline=presenter)
    try:
        presenter.others = [(480, 140, 120, 160)]
        report = _check(session, presenter, 6, 0)
        assert report.reason == "MULTIPLE_FACES"
    finally:
        session.close()
