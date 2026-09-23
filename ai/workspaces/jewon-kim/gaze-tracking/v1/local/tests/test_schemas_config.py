"""Contracts protected by this file: ``vision.schemas`` + ``vision.config``.

``schemas`` is the single source of truth for the sign convention and for every
structure that crosses a module boundary, so the tests below lock in

* the angle <-> unit-vector projection and its documented consequences
  (a downward gaze is a NEGATIVE pitch, so BOTTOM sits below CAMERA; a rightward
  gaze is a POSITIVE yaw), including the poles and degenerate directions;
* the frozen wire shapes -- ``GazeStateEvent.to_dict`` is exactly seven keys and
  must never leak the debug fields, ``AiVersion.to_dict`` is wrapped, and
  ``from_dict`` drops keys an older revision left behind;
* the small guarded properties (``backlight_ratio`` at a black face,
  ``min_eye_openness``, ``p_max`` / ``margin``, half-open ``SegmentLabel``).

``config`` is the reproducibility surface: the file-to-section mapping (only
``gaze_backbone.yaml -> backbone`` differs), the deliberate degrade-to-defaults
behaviour on a missing directory or an unknown key, and the 12-hex config hash
that experiment provenance is keyed on.

No dataset is fabricated anywhere here: every input is either a hand-built value
whose purpose is one specific code path, or the shipped ``ai/configs`` tree.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import fields
from pathlib import Path

import numpy as np
import pytest

from vision.config import (
    AI_ROOT,
    CONFIG_DIR,
    REPO_ROOT,
    BackboneConfig,
    CalibrationConfig,
    CollectionConfig,
    PlacementConfig,
    PreprocessConfig,
    ProtocolCondition,
    ReleaseGateConfig,
    TemporalConfig,
    VisionConfig,
    load_config,
    resolve_path,
)
from vision.schemas import (
    DECISION_CLASSES,
    INVERTED_PITCH_HINT_PREFIX,
    AiVersion,
    CalibrationFailReason,
    CalibrationQuality,
    CalibrationSample,
    CalibrationStatus,
    FrameObservation,
    FrameQuality,
    GazeDecision,
    GazeLabel,
    GazeState,
    GazeStateEvent,
    GazeVector,
    HeadPose,
    InvalidReason,
    ManifestRecord,
    ParticipantMeta,
    SegmentLabel,
    clamp_unit,
    unit_vector_to_angles,
)

# ``to_unit_vector`` returns float32, so a full round trip cannot beat ~1e-7.
FLOAT32_ANGLE_TOL = 2e-6

YAW_GRID_DEG = (-170.0, -95.0, -44.0, -1.0, 0.0, 1.0, 37.0, 90.0, 179.0)
PITCH_GRID_DEG = (-89.0, -60.0, -12.0, 0.0, 12.0, 60.0, 89.0)


def _gaze(yaw_deg: float, pitch_deg: float) -> GazeVector:
    return GazeVector(
        gaze_yaw=math.radians(yaw_deg),
        gaze_pitch=math.radians(pitch_deg),
        confidence=1.0,
    )


# ==========================================================================
# schemas: the angle <-> unit vector projection
# ==========================================================================


@pytest.mark.parametrize("yaw_deg", YAW_GRID_DEG)
@pytest.mark.parametrize("pitch_deg", PITCH_GRID_DEG)
def test_angles_survive_a_round_trip_through_the_unit_vector(yaw_deg, pitch_deg):
    """(yaw, pitch) -> g -> (yaw, pitch) is the identity away from the poles."""
    gaze = _gaze(yaw_deg, pitch_deg)
    yaw_back, pitch_back = unit_vector_to_angles(gaze.to_unit_vector())

    assert yaw_back == pytest.approx(gaze.gaze_yaw, abs=FLOAT32_ANGLE_TOL)
    assert pitch_back == pytest.approx(gaze.gaze_pitch, abs=FLOAT32_ANGLE_TOL)


@pytest.mark.parametrize("yaw_deg", YAW_GRID_DEG)
@pytest.mark.parametrize("pitch_deg", PITCH_GRID_DEG)
def test_to_unit_vector_is_normalised_and_float32(yaw_deg, pitch_deg):
    vec = _gaze(yaw_deg, pitch_deg).to_unit_vector()

    assert vec.shape == (3,)
    assert vec.dtype == np.float32
    assert float(np.linalg.norm(vec)) == pytest.approx(1.0, abs=1e-6)


def test_zero_angles_point_straight_down_the_optical_axis():
    """The zero of the convention is +z, i.e. straight into the scene."""
    np.testing.assert_allclose(_gaze(0.0, 0.0).to_unit_vector(), [0.0, 0.0, 1.0], atol=1e-7)


@pytest.mark.parametrize(
    "vec, expect_pitch_sign, why",
    [
        ((0.0, 1.0, 4.0), -1, "+y is DOWN in the image, so a downward gaze is negative pitch"),
        ((0.0, -1.0, 4.0), +1, "-y is UP, so an upward gaze is positive pitch"),
    ],
)
def test_downward_gaze_is_negative_pitch(vec, expect_pitch_sign, why):
    _, pitch = unit_vector_to_angles(vec)
    assert math.copysign(1.0, pitch) == expect_pitch_sign, why


@pytest.mark.parametrize(
    "vec, expect_yaw_sign, why",
    [
        ((1.0, 0.0, 4.0), +1, "+x is image right, and yaw = atan2(x, z)"),
        ((-1.0, 0.0, 4.0), -1, "-x is image left"),
    ],
)
def test_rightward_gaze_is_positive_yaw(vec, expect_yaw_sign, why):
    yaw, _ = unit_vector_to_angles(vec)
    assert math.copysign(1.0, yaw) == expect_yaw_sign, why


def test_bottom_gaze_has_a_more_negative_pitch_than_camera_gaze():
    """The hard consequence every backbone adapter must respect (module docstring).

    Reading a script below the screen means the direction has a positive image-y
    component, which the projection turns into the smaller (more negative) pitch.
    """
    camera_yaw, camera_pitch = unit_vector_to_angles((0.0, 0.00, 1.0))
    bottom_yaw, bottom_pitch = unit_vector_to_angles((0.0, 0.35, 1.0))

    assert bottom_pitch < camera_pitch
    assert bottom_pitch < 0.0 <= camera_pitch
    # Looking down does not drag the yaw off the vertical plane.
    assert bottom_yaw == pytest.approx(camera_yaw, abs=1e-12)


@pytest.mark.parametrize(
    "vec, expected_pitch",
    [
        ((0.0, -1.0, 0.0), +math.pi / 2),  # straight up
        ((0.0, +1.0, 0.0), -math.pi / 2),  # straight down
    ],
)
def test_poles_give_the_exact_right_angle_pitch(vec, expected_pitch):
    """asin is clipped, so |y| == 1 is exact rather than a domain error.

    Yaw is mathematically undefined at a pole and is deliberately not asserted.
    """
    _, pitch = unit_vector_to_angles(vec)
    assert pitch == pytest.approx(expected_pitch, abs=1e-12)


@pytest.mark.parametrize(
    "vec",
    [
        (0.0, 0.0, 0.0),
        (1e-12, -1e-12, 1e-12),
        (),
        np.zeros(3, dtype=np.float32),
    ],
    ids=["exact_zero", "below_norm_epsilon", "empty", "zero_float32"],
)
def test_degenerate_direction_returns_neutral_angles_instead_of_nan(vec):
    """A zero-length direction must not divide by zero; it collapses to (0, 0)."""
    assert unit_vector_to_angles(vec) == (0.0, 0.0)


@pytest.mark.parametrize(
    "vec",
    [
        (0.0, 1.0, 0.0),
        (0.0, -1.0, 0.0),
        (0.0, 1e-9, 0.0),  # exactly on the norm guard, so it is still divided
        (1e30, 1e30, 1e30),
        (-3.0, 0.0, -4.0),
    ],
)
def test_normalisation_never_pushes_asin_out_of_domain(vec):
    """Rounding in ``v / norm`` can nudge |y| past 1; the clip must absorb it."""
    yaw, pitch = unit_vector_to_angles(vec)
    assert math.isfinite(yaw) and math.isfinite(pitch)
    assert -math.pi / 2 <= pitch <= math.pi / 2
    assert -math.pi <= yaw <= math.pi


def test_unit_vector_to_angles_does_not_consume_the_callers_array():
    vec = np.array([0.3, 0.4, 0.5], dtype=np.float64)
    before = vec.copy()
    unit_vector_to_angles(vec)
    np.testing.assert_array_equal(vec, before)


def test_nan_direction_does_not_become_a_confident_downward_gaze():
    """The clip used to map NaN to 1.0, i.e. a confident straight-down gaze.

    ``max(-1, min(1, nan))`` is ``1.0`` (``nan < 1.0`` is False, so ``min`` keeps
    its first argument), so a NaN direction returned pitch ``-pi/2``: the
    strongest possible false BOTTOM, from a frame that produced no direction.
    """
    _, pitch = unit_vector_to_angles((float("nan"),) * 3)
    assert math.isnan(pitch) or pitch == 0.0


@pytest.mark.parametrize(
    "vec",
    [
        (float("nan"),) * 3,
        (float("nan"), 0.0, 1.0),
        (0.0, float("nan"), 1.0),
        (0.0, 0.0, float("nan")),
        (float("inf"), 0.0, 1.0),
        (0.0, float("-inf"), 1.0),
    ],
)
def test_a_non_finite_component_collapses_to_the_no_direction_answer(vec):
    """Any unusable component gives the same (0, 0) the zero-length guard gives.

    Half-answers are the danger here: a caller that only checks the pitch (or
    only the yaw) with ``math.isfinite`` would otherwise accept the other half.
    """
    assert unit_vector_to_angles(vec) == (0.0, 0.0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_clamp_unit_refuses_a_non_finite_value_instead_of_clipping_it(bad):
    """``None`` is the "no answer"; a NaN would flow on through the arithmetic."""
    assert clamp_unit(bad) is None


@pytest.mark.parametrize(
    "value, expected",
    [(2.0, 1.0), (1.0 + 1e-12, 1.0), (-2.0, -1.0), (-1.0 - 1e-12, -1.0)],
)
def test_clamp_unit_clips_an_out_of_domain_value_to_the_asin_domain(value, expected):
    assert clamp_unit(value) == expected


@pytest.mark.parametrize("value", [-1.0, -0.5, -0.0, 0.0, 1e-300, 0.5, 1.0])
def test_clamp_unit_returns_an_in_domain_value_bit_identically(value):
    """The finite path must not move a single bit; -0.0 keeps its sign too."""
    clamped = clamp_unit(value)
    assert clamped == value
    assert math.copysign(1.0, clamped) == math.copysign(1.0, value)


def test_gaze_vector_degree_properties_are_the_radian_fields_converted():
    gaze = GazeVector(gaze_yaw=math.radians(-12.5), gaze_pitch=math.radians(7.25), confidence=0.4)
    assert gaze.gaze_yaw_deg == pytest.approx(-12.5)
    assert gaze.gaze_pitch_deg == pytest.approx(7.25)


def test_gaze_vector_as_array_is_yaw_then_pitch():
    """Feature builders index this positionally; the order is part of the contract."""
    gaze = GazeVector(gaze_yaw=0.25, gaze_pitch=-0.75, confidence=1.0)
    arr = gaze.as_array()
    assert arr.dtype == np.float32
    assert arr.tolist() == pytest.approx([0.25, -0.75])


def test_head_pose_as_array_is_yaw_pitch_roll_and_omits_the_diagnostics():
    pose = HeadPose(yaw=0.1, pitch=0.2, roll=0.3, reprojection_error=9.0, depth_proxy=2.0)
    assert pose.as_array().tolist() == pytest.approx([0.1, 0.2, 0.3])
    assert set(pose.to_dict()) == {"yaw", "pitch", "roll", "reprojection_error", "depth_proxy"}


def test_calibration_sample_raw_vector_is_gaze_then_head_in_a_fixed_order():
    sample = CalibrationSample(
        label=GazeLabel.BOTTOM.value,
        gaze=GazeVector(gaze_yaw=1.0, gaze_pitch=2.0, confidence=1.0),
        head_pose=HeadPose(yaw=3.0, pitch=4.0, roll=5.0),
    )
    assert sample.raw_vector().tolist() == pytest.approx([1.0, 2.0, 3.0, 4.0, 5.0])
    assert sample.raw_vector().dtype == np.float32


# ==========================================================================
# schemas: label enums
# ==========================================================================


@pytest.mark.parametrize("enum_cls", [GazeLabel, GazeState])
@pytest.mark.parametrize("raw", ["camera", "  CAMERA  ", "\tCaMeRa\n", "CAMERA"])
def test_coerce_normalises_case_and_surrounding_whitespace(enum_cls, raw):
    assert enum_cls.coerce(raw) is enum_cls.CAMERA
    assert enum_cls.coerce(raw).value == "CAMERA"


@pytest.mark.parametrize("enum_cls", [GazeLabel, GazeState])
def test_coerce_returns_its_own_member_unchanged(enum_cls):
    assert enum_cls.coerce(enum_cls.BOTTOM) is enum_cls.BOTTOM


@pytest.mark.parametrize(
    "enum_cls, foreign_value",
    [(GazeLabel, "UNCERTAIN"), (GazeState, "IGNORE")],
)
def test_coerce_rejects_the_other_enums_exclusive_member(enum_cls, foreign_value):
    """The two vocabularies overlap on CAMERA/BOTTOM only; the rest must not cross.

    IGNORE is a ground-truth-only label and UNCERTAIN a runtime-only state, so
    silently accepting one for the other would corrupt evaluation.
    """
    with pytest.raises(ValueError):
        enum_cls.coerce(foreign_value)


@pytest.mark.parametrize("enum_cls", [GazeLabel, GazeState])
@pytest.mark.parametrize(
    "bad", ["", "   ", "camra", "CAMERA_", "camera bottom", None, 3, 0.0, True],
)
def test_coerce_rejects_junk_loudly(enum_cls, bad):
    with pytest.raises(ValueError):
        enum_cls.coerce(bad)


def test_coerce_accepts_the_wire_value_of_the_sibling_enum():
    """``.value`` is the only spelling that crosses the two enums safely."""
    assert GazeLabel.coerce(GazeState.CAMERA.value) is GazeLabel.CAMERA
    assert GazeState.coerce(GazeLabel.BOTTOM.value) is GazeState.BOTTOM


def test_coerce_accepts_a_sibling_enum_member_that_is_already_the_right_string():
    """``str(member)`` is 'GazeState.CAMERA', so coerce unwraps ``.value`` first.

    The fields are typed as plain ``str`` everywhere, and these members really
    are strings, so a sibling member type-checks at the call site; it used to
    explode in here instead.
    """
    assert GazeState.CAMERA == "CAMERA"  # it really is that string
    assert GazeLabel.coerce(GazeState.CAMERA) is GazeLabel.CAMERA
    assert GazeState.coerce(GazeLabel.BOTTOM) is GazeState.BOTTOM


def test_unwrapping_a_sibling_member_still_respects_the_two_vocabularies():
    """Unwrapping ``.value`` must not become a free pass between the enums."""
    with pytest.raises(ValueError):
        GazeLabel.coerce(GazeState.UNCERTAIN)
    with pytest.raises(ValueError):
        GazeState.coerce(GazeLabel.IGNORE)
    with pytest.raises(ValueError):
        GazeLabel.coerce(InvalidReason.NO_FACE)


def test_decision_classes_are_camera_then_bottom():
    """p_camera / p_bottom are read off the classifier by this index order."""
    assert DECISION_CLASSES == ("CAMERA", "BOTTOM")
    assert DECISION_CLASSES[0] == GazeLabel.CAMERA.value
    assert GazeLabel.IGNORE.value not in DECISION_CLASSES


@pytest.mark.parametrize(
    "enum_cls",
    [GazeLabel, GazeState, InvalidReason, CalibrationStatus, CalibrationFailReason],
)
def test_every_label_enum_serialises_as_its_bare_wire_string(enum_cls):
    """These land in JSON manifests; ``json.dumps`` must not emit 'Cls.MEMBER'."""
    for member in enum_cls:
        assert json.dumps(member) == json.dumps(member.value)
        assert member.name == member.value


# ==========================================================================
# schemas: FrameQuality guards
# ==========================================================================


@pytest.mark.parametrize(
    "face, background, expected",
    [
        (100.0, 160.0, 1.6),
        (100.0, 100.0, 1.0),
        (50.0, 10.0, 0.2),
        (2e-6, 2e-6, 1.0),  # just above the guard, still a real ratio
    ],
)
def test_backlight_ratio_is_background_over_face_luminance(face, background, expected):
    quality = FrameQuality(face_brightness=face, background_brightness=background)
    assert quality.backlight_ratio == pytest.approx(expected)


@pytest.mark.parametrize("face", [0.0, 1e-6, 1e-9, -0.0, -12.0])
def test_backlight_ratio_guard_replaces_a_division_by_a_dark_face(face):
    quality = FrameQuality(face_brightness=face, background_brightness=255.0)
    assert quality.backlight_ratio == 0.0
    assert isinstance(quality.backlight_ratio, float)


@pytest.mark.parametrize(
    "left, right, expected",
    [(0.3, 0.1, 0.1), (0.1, 0.3, 0.1), (0.2, 0.2, 0.2), (0.0, 0.4, 0.0)],
)
def test_min_eye_openness_takes_the_worse_eye(left, right, expected):
    """One closed eye is enough to call the frame a blink, so it is min not mean."""
    quality = FrameQuality(left_eye_openness=left, right_eye_openness=right)
    assert quality.min_eye_openness == pytest.approx(expected)


def test_quality_to_dict_adds_backlight_ratio_and_nothing_else():
    """``data.features_table`` mirrors these as its frozen ``q_*`` columns."""
    quality = FrameQuality(face_brightness=100.0, background_brightness=200.0)
    out = quality.to_dict()

    assert set(out) == {f.name for f in fields(FrameQuality)} | {"backlight_ratio"}
    assert out["backlight_ratio"] == pytest.approx(2.0)
    # min_eye_openness is a property too, and is deliberately NOT serialised.
    assert "min_eye_openness" not in out


def test_quality_defaults_describe_a_usable_frame():
    quality = FrameQuality()
    assert quality.landmark_visibility == 1.0
    assert quality.touches_border is False
    assert quality.backlight_ratio == 0.0


# ==========================================================================
# schemas: FrameObservation
# ==========================================================================


def _observation(**kwargs) -> FrameObservation:
    base = dict(frame_id=7, t_ms=875, face_confidence=0.91, face_valid=True)
    base.update(kwargs)
    return FrameObservation(**base)


def test_observation_defaults_are_not_shared_between_instances():
    """``field(default_factory=...)`` guards a classic mutable-default bug."""
    first, second = _observation(), _observation()
    first.head_pose.yaw = 1.234
    first.quality.face_brightness = 42.0

    assert second.head_pose.yaw == 0.0
    assert second.quality.face_brightness == 0.0
    assert first.head_pose is not second.head_pose
    assert first.quality is not second.quality


def test_to_record_is_the_frozen_pixel_free_row():
    """Adding a field here also has to be added to the features-table columns."""
    obs = _observation(
        head_pose=HeadPose(yaw=0.1, pitch=-0.2, roll=0.05, reprojection_error=3.0, depth_proxy=1.5),
        quality=FrameQuality(face_brightness=120.0, background_brightness=60.0),
        face_crop=np.zeros((4, 4, 3), dtype=np.uint8),
        landmarks=np.zeros((478, 3), dtype=np.float32),
        blendshapes={"eyeLookDownLeft": 0.4},
        invalid_reason=InvalidReason.EYES_CLOSED.value,
        preprocess_ms=4.5,
    )
    record = obs.to_record()

    assert set(record) == {
        "frame_id",
        "t_ms",
        "face_confidence",
        "face_valid",
        "head_yaw",
        "head_pitch",
        "head_roll",
        "head_reprojection_error",
        "head_depth_proxy",
        "invalid_reason",
        "preprocess_ms",
        "q_face_area_ratio",
        "q_face_brightness",
        "q_background_brightness",
        "q_face_contrast",
        "q_left_eye_openness",
        "q_right_eye_openness",
        "q_landmark_visibility",
        "q_touches_border",
        "q_backlight_ratio",
    }
    assert record["head_pitch"] == pytest.approx(-0.2)
    assert record["q_backlight_ratio"] == pytest.approx(0.5)
    assert not any(isinstance(v, np.ndarray) for v in record.values())
    json.dumps(record)  # must survive the manifest/cache writer


def test_to_record_reflects_later_edits_rather_than_a_stale_snapshot():
    obs = _observation()
    obs.head_pose.pitch = -0.42
    assert obs.to_record()["head_pitch"] == pytest.approx(-0.42)


# ==========================================================================
# schemas: GazeDecision
# ==========================================================================


@pytest.mark.parametrize(
    "p_camera, p_bottom, p_max, margin",
    [
        (0.9, 0.1, 0.9, 0.8),
        (0.1, 0.9, 0.9, 0.8),
        (0.5, 0.5, 0.5, 0.0),
        (0.55, 0.45, 0.55, 0.1),
    ],
)
def test_p_max_and_margin_are_orientation_free(p_camera, p_bottom, p_max, margin):
    decision = GazeDecision(
        t_ms=0, frame_id=0, label="CAMERA", p_camera=p_camera, p_bottom=p_bottom, face_valid=True
    )
    assert decision.p_max == pytest.approx(p_max)
    assert decision.margin == pytest.approx(margin)
    # The doc 5-4 UNCERTAIN rule leans on both; for a 2-class posterior they are
    # the same statistic, which is why one threshold cannot replace the other.
    assert decision.margin == pytest.approx(2.0 * decision.p_max - 1.0)


def test_decision_to_dict_omits_the_gaze_block_when_there_is_no_gaze():
    decision = GazeDecision(
        t_ms=125, frame_id=1, label="UNCERTAIN", p_camera=0.0, p_bottom=0.0, face_valid=False,
        uncertain_reason=InvalidReason.NO_FACE.value,
    )
    out = decision.to_dict()

    assert "gaze" not in out
    assert set(out) == {
        "t_ms", "frame_id", "label", "p_camera", "p_bottom",
        "face_valid", "uncertain_reason", "latency_ms",
    }
    assert out["uncertain_reason"] == "NO_FACE"


def test_decision_to_dict_embeds_the_gaze_as_a_nested_plain_dict():
    gaze = GazeVector(gaze_yaw=0.1, gaze_pitch=-0.3, confidence=0.8, backbone="mediapipe_geom")
    out = GazeDecision(
        t_ms=0, frame_id=0, label="BOTTOM", p_camera=0.2, p_bottom=0.8, face_valid=True, gaze=gaze
    ).to_dict()

    assert out["gaze"] == {
        "gaze_yaw": 0.1,
        "gaze_pitch": -0.3,
        "confidence": 0.8,
        "backbone": "mediapipe_geom",
        "inference_ms": 0.0,
    }
    json.dumps(out)


# ==========================================================================
# schemas: GazeStateEvent -- the frozen public event
# ==========================================================================

EVENT_CONTRACT_KEYS = {
    "type",
    "model_version",
    "t_ms",
    "label",
    "confidence",
    "continuous_duration_ms",
    "face_valid",
}


def _event(**kwargs) -> GazeStateEvent:
    base = dict(
        t_ms=1000, label=GazeState.BOTTOM.value, confidence=0.5,
        continuous_duration_ms=600, face_valid=True,
    )
    base.update(kwargs)
    return GazeStateEvent(**base)


def test_event_to_dict_is_exactly_the_seven_key_doc_contract():
    out = _event(is_transition=True, smoothed_p_camera=0.2, smoothed_p_bottom=0.8).to_dict()

    assert set(out) == EVENT_CONTRACT_KEYS
    assert len(out) == 7
    # Debug-only fields must never reach a downstream consumer.
    for leaked in ("is_transition", "smoothed_p_camera", "smoothed_p_bottom"):
        assert leaked not in out


def test_event_to_dict_defaults_carry_the_type_and_model_version():
    out = _event().to_dict()
    assert out["type"] == "GAZE_STATE"
    assert out["model_version"] == "gaze_v1.0.0"


def test_event_to_dict_rounds_confidence_and_normalises_numeric_types():
    """The smoother hands over numpy scalars; the wire shape must stay JSON-safe."""
    out = _event(
        t_ms=np.int64(1234),
        confidence=np.float32(0.123456789),
        continuous_duration_ms=np.int64(600),
        face_valid=np.bool_(True),
    ).to_dict()

    assert out["confidence"] == pytest.approx(0.1235, abs=1e-9)
    assert type(out["t_ms"]) is int
    assert type(out["continuous_duration_ms"]) is int
    assert type(out["face_valid"]) is bool
    json.dumps(out)


def test_event_to_dict_truncates_a_fractional_timestamp_toward_zero():
    assert _event(t_ms=1999.9).to_dict()["t_ms"] == 1999


def test_event_label_serialises_as_the_bare_state_string():
    out = _event(label=GazeState.CAMERA).to_dict()
    assert json.loads(json.dumps(out))["label"] == "CAMERA"


def test_debug_dict_is_the_contract_plus_exactly_three_debug_keys():
    event = _event(confidence=0.987654, is_transition=True,
                   smoothed_p_camera=0.111111, smoothed_p_bottom=0.888888)
    contract, debug = event.to_dict(), event.to_debug_dict()

    assert set(debug) - set(contract) == {"is_transition", "smoothed_p_camera", "smoothed_p_bottom"}
    assert {k: debug[k] for k in contract} == contract  # never rewrites a shared key
    assert debug["is_transition"] is True
    assert debug["smoothed_p_camera"] == pytest.approx(0.1111)
    assert debug["smoothed_p_bottom"] == pytest.approx(0.8889)


def test_debug_dict_does_not_alias_the_contract_dict():
    event = _event()
    debug = event.to_debug_dict()
    debug["label"] = "MUTATED"
    assert event.to_dict()["label"] == GazeState.BOTTOM.value


# ==========================================================================
# schemas: calibration quality
# ==========================================================================


@pytest.mark.parametrize(
    "status, expected",
    [
        (CalibrationStatus.OK.value, True),
        (CalibrationStatus.RETRY_REQUIRED.value, False),
        ("ok", False),  # comparison is exact; a lowercase status is not a pass
        ("", False),
    ],
)
def test_calibration_ok_is_an_exact_status_match(status, expected):
    assert CalibrationQuality(status=status, n_camera=10, n_bottom=10).ok is expected


def test_calibration_to_dict_duplicates_status_under_the_ui_key():
    out = CalibrationQuality(status="RETRY_REQUIRED", n_camera=3, n_bottom=9).to_dict()
    assert out["calibration_status"] == out["status"] == "RETRY_REQUIRED"
    assert set(out) == {f.name for f in fields(CalibrationQuality)} | {"calibration_status"}


def test_inverted_pitch_is_advisory_and_never_a_failure_reason():
    """Doc: the ordering warning rides on ``hint``; ``status``/``reason`` stay clean."""
    quality = CalibrationQuality(
        status=CalibrationStatus.OK.value,
        n_camera=30,
        n_bottom=30,
        hint=f"{INVERTED_PITCH_HINT_PREFIX} BOTTOM pitch is above CAMERA",
    )
    assert quality.ok is True
    assert quality.reason is None
    assert quality.hint.startswith(INVERTED_PITCH_HINT_PREFIX)
    # The prefix must stay parseable as the enum name it stands for.
    assert INVERTED_PITCH_HINT_PREFIX == f"{CalibrationFailReason.INVERTED_PITCH.value}:"


# ==========================================================================
# schemas: dataset records
# ==========================================================================


def _segment(**kwargs) -> SegmentLabel:
    base = dict(participant_id="p01", session_id="s1", start_ms=1000, end_ms=2000, label="BOTTOM")
    base.update(kwargs)
    return SegmentLabel(**base)


@pytest.mark.parametrize(
    "t_ms, inside",
    [
        (999, False),
        (1000, True),   # start is inclusive
        (1500, True),
        (1999, True),
        (2000, False),  # end is exclusive, so adjacent segments cannot both claim it
        (2001, False),
    ],
)
def test_segment_contains_is_a_half_open_interval(t_ms, inside):
    assert _segment().contains(t_ms) is inside


def test_adjacent_segments_claim_every_instant_exactly_once():
    first, second = _segment(start_ms=0, end_ms=1000), _segment(start_ms=1000, end_ms=2000)
    for t_ms in (0, 999, 1000, 1999):
        assert first.contains(t_ms) + second.contains(t_ms) == 1


@pytest.mark.parametrize("start, end", [(500, 500), (900, 100)])
def test_empty_or_inverted_segment_contains_nothing(start, end):
    segment = _segment(start_ms=start, end_ms=end)
    assert not any(segment.contains(t) for t in range(0, 1200, 25))
    assert segment.duration_ms == end - start


def test_segment_duration_is_the_half_open_length():
    assert _segment(start_ms=1000, end_ms=2500).duration_ms == 1500


@pytest.mark.parametrize(
    "cls, payload",
    [
        (
            SegmentLabel,
            dict(participant_id="p01", session_id="s1", start_ms=0, end_ms=10, label="CAMERA"),
        ),
        (
            ManifestRecord,
            dict(
                sample_id="p01_s1_000",
                participant_id="p01",
                session_id="s1",
                timestamp_ms=0,
                frame_path="frames/000.jpg",
                gaze_label="CAMERA",
            ),
        ),
    ],
    ids=["SegmentLabel", "ManifestRecord"],
)
def test_from_dict_drops_keys_an_older_revision_left_behind(cls, payload):
    """A stale manifest column must degrade to defaults, not blow up the loader."""
    noisy = dict(payload, retired_column=1, __proto__="x", label_version=None)
    obj = cls.from_dict(noisy)

    assert not hasattr(obj, "retired_column")
    assert obj.to_dict() == cls.from_dict(obj.to_dict()).to_dict()
    assert set(obj.to_dict()) == {f.name for f in fields(cls)}


@pytest.mark.parametrize("cls", [SegmentLabel, ManifestRecord])
def test_from_dict_still_requires_the_mandatory_columns(cls):
    with pytest.raises(TypeError):
        cls.from_dict({"participant_id": "p01"})


def test_from_dict_does_not_mutate_the_caller_mapping():
    payload = dict(
        participant_id="p01", session_id="s1", start_ms=0, end_ms=10, label="CAMERA", junk=1
    )
    before = copy.deepcopy(payload)
    SegmentLabel.from_dict(payload)
    assert payload == before


def test_dataset_record_defaults_agree_across_the_two_row_types():
    """Manifest rows and segment rows are joined on these; drift would split them."""
    segment, record = _segment(), ManifestRecord(
        sample_id="x", participant_id="p01", session_id="s1", timestamp_ms=0,
        frame_path="f.jpg", gaze_label="CAMERA",
    )
    for name in ("condition", "glasses", "lighting", "device_group", "label_version"):
        assert getattr(segment, name) == getattr(record, name)


def test_participant_meta_defaults_describe_the_expected_rig():
    meta = ParticipantMeta(participant_id="p01")
    assert meta.camera_position == "top_center"
    assert meta.to_dict()["participant_id"] == "p01"


def test_ai_version_to_dict_is_wrapped_under_a_single_key():
    """Takes embed this verbatim, so the wrapper is the contract, not the payload."""
    out = AiVersion(gaze_backbone="mediapipe_geom", config_hash="0123456789ab").to_dict()

    assert set(out) == {"ai_version"}
    assert set(out["ai_version"]) == {f.name for f in fields(AiVersion)}
    assert out["ai_version"]["config_hash"] == "0123456789ab"
    assert out["ai_version"]["gaze_backbone"] == "mediapipe_geom"
    json.dumps(out)


def test_ai_version_defaults_leave_the_provenance_fields_obviously_unset():
    inner = AiVersion().to_dict()["ai_version"]
    assert inner["gaze_backbone"] == "unset"
    assert inner["config_hash"] == ""
    assert inner["code_commit"] == "unknown"


def test_version_defaults_do_not_drift_from_the_constants_that_own_them():
    """schemas cannot import runtime, so these literals are duplicated by hand.

    They are the values stamped on every stored take; a silent divergence would
    make two artefacts of the same run claim different versions.
    """
    from vision.runtime.version import CLASSIFIER_VERSION, MODEL_VERSION
    from vision.temporal.smoother import TEMPORAL_RULE_VERSION

    assert GazeStateEvent(0, "CAMERA", 0.0, 0, True).model_version == MODEL_VERSION
    assert AiVersion().gaze_classifier == CLASSIFIER_VERSION
    assert AiVersion().temporal_rule == TEMPORAL_RULE_VERSION


# ==========================================================================
# config: file -> section mapping and degrade-to-defaults
# ==========================================================================


def _write(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body, encoding="utf-8")


def test_backbone_section_is_fed_by_gaze_backbone_yaml_only(tmp_path):
    """The one filename that differs from its section name (doc mapping)."""
    _write(tmp_path, "gaze_backbone.yaml", "name: l2cs\ndevice: cuda\nnum_threads: 4\n")
    _write(tmp_path, "backbone.yaml", "name: DECOY_NEVER_READ\n")

    cfg = load_config(tmp_path)

    assert cfg.backbone.name == "l2cs"
    assert cfg.backbone.device == "cuda"
    assert cfg.backbone.num_threads == 4
    assert cfg.backbone.batch_size == BackboneConfig.batch_size  # untouched default


@pytest.mark.parametrize(
    "filename, section, key, value",
    [
        ("preprocess.yaml", "preprocess", "face_crop_size", 128),
        ("gaze_backbone.yaml", "backbone", "name", "gazetr"),
        ("placement.yaml", "placement", "mode", "geometric"),
        ("calibration.yaml", "calibration", "feature_set", "A"),
        ("temporal.yaml", "temporal", "window_frames", 3),
        ("release_gate.yaml", "release_gate", "min_macro_f1", 0.5),
        ("collection.yaml", "collection", "record_fps", 60),
    ],
)
def test_every_config_file_lands_on_its_documented_section(tmp_path, filename, section, key, value):
    _write(tmp_path, filename, f"{key}: {json.dumps(value)}\n")
    cfg = load_config(tmp_path)

    assert getattr(getattr(cfg, section), key) == value
    # Nothing else moved: the rest of the tree is still the default config.
    other = {f.name for f in fields(VisionConfig)} - {section}
    defaults = VisionConfig()
    for name in other:
        assert getattr(cfg, name) == getattr(defaults, name)


@pytest.mark.parametrize("where", ["empty_dir", "missing_dir"])
def test_a_config_dir_with_no_yaml_yields_defaults_without_raising(tmp_path, where):
    directory = tmp_path / "configs"
    if where == "empty_dir":
        directory.mkdir()

    cfg = load_config(directory)

    assert cfg == VisionConfig()
    assert cfg.collection.conditions == []
    assert cfg.hash() == VisionConfig().hash()


def test_unknown_yaml_keys_are_dropped_instead_of_raising(tmp_path):
    _write(tmp_path, "temporal.yaml", "window_frames: 12\nretired_knob: 99\nema_alpha: 0.5\n")
    cfg = load_config(tmp_path)

    assert cfg.temporal.window_frames == 12
    assert cfg.temporal.ema_alpha == 0.5
    assert not hasattr(cfg.temporal, "retired_knob")


def test_an_empty_yaml_file_is_treated_as_no_overrides(tmp_path):
    _write(tmp_path, "calibration.yaml", "")
    _write(tmp_path, "temporal.yaml", "# only a comment\n")
    cfg = load_config(tmp_path)

    assert cfg.calibration == CalibrationConfig()
    assert cfg.temporal == TemporalConfig()


def test_a_non_mapping_yaml_section_does_not_replace_the_dataclass(tmp_path):
    """A stray ``- `` turns the file into a list; that must fail at load time.

    It used to be handed straight through, so ``cfg.preprocess`` BECAME the
    list and the config still looked loadable: the failure surfaced far away as
    an AttributeError naming neither the file nor the section.  The error now
    names the section and the type found.
    """
    _write(tmp_path, "preprocess.yaml", "- analysis_fps: 8.0\n")

    with pytest.raises(TypeError) as excinfo:
        load_config(tmp_path)

    message = str(excinfo.value)
    assert "preprocess" in message
    assert "list" in message


@pytest.mark.parametrize(
    "body, bad_type",
    [
        ("- key: a\n  seconds: 2\n", "list"),
        ("just a string\n", "str"),
    ],
)
def test_a_malformed_collection_section_is_rejected_by_name(tmp_path, body, bad_type):
    """collection has its own build path (conditions are split off) -- same rule."""
    _write(tmp_path, "collection.yaml", body)

    with pytest.raises(TypeError) as excinfo:
        load_config(tmp_path)

    assert "collection" in str(excinfo.value)
    assert bad_type in str(excinfo.value)


def test_a_malformed_protocol_condition_is_rejected_with_its_index(tmp_path):
    _write(
        tmp_path,
        "collection.yaml",
        "conditions:\n  - key: ok\n    seconds: 2\n  - nope\n",
    )

    with pytest.raises(TypeError) as excinfo:
        load_config(tmp_path)

    assert "conditions[1]" in str(excinfo.value)


# ==========================================================================
# config: overrides
# ==========================================================================


def test_overrides_are_keyed_by_section_not_by_filename(tmp_path):
    cfg = load_config(
        tmp_path,
        overrides={"backbone": {"name": "l2cs"}, "gaze_backbone": {"name": "IGNORED"}},
    )
    assert cfg.backbone.name == "l2cs"


def test_overrides_merge_into_yaml_without_dropping_the_untouched_keys(tmp_path):
    _write(tmp_path, "temporal.yaml", "window_frames: 8\nema_alpha: 0.35\nheartbeat_ms: 1000\n")

    cfg = load_config(tmp_path, overrides={"temporal": {"ema_alpha": 0.9}})

    assert cfg.temporal.ema_alpha == 0.9
    assert cfg.temporal.window_frames == 8  # from the file
    assert cfg.temporal.heartbeat_ms == 1000


def test_an_unknown_override_section_is_ignored_rather_than_raising(tmp_path):
    cfg = load_config(tmp_path, overrides={"not_a_section": {"x": 1}})
    assert cfg == VisionConfig()


def test_load_config_does_not_mutate_the_overrides_it_was_given(tmp_path):
    _write(tmp_path, "temporal.yaml", "window_frames: 8\n")
    overrides = {"temporal": {"ema_alpha": 0.9}, "collection": {"conditions": [{"key": "a", "seconds": 1}]}}
    before = copy.deepcopy(overrides)

    load_config(tmp_path, overrides=overrides)

    assert overrides == before


def test_two_configs_from_one_overrides_dict_do_not_share_mutable_values(tmp_path):
    """_deep_merge copies mappings but used to assign lists by reference.

    Two configs loaded from one overrides dict then shared the list, so editing
    one arm's config silently moved the OTHER arm's ``config_hash`` -- the doc 18
    provenance key -- and its recorded provenance stopped describing its run.
    """
    overrides = {"preprocess": {"eye_crop_size": [8, 8]}}
    first = load_config(tmp_path, overrides=overrides)
    second = load_config(tmp_path, overrides=overrides)
    hash_before = second.hash()

    first.preprocess.eye_crop_size.append(99)

    assert second.preprocess.eye_crop_size == [8, 8]
    assert second.hash() == hash_before


def test_a_config_does_not_alias_the_overrides_dict_it_came_from(tmp_path):
    """The same aliasing seen from the other side: mutate ``overrides`` after.

    ``backbone.params`` is the dict half of the bug -- _deep_merge only recurses
    when the YAML carries a matching mapping, and here the file is absent.
    """
    overrides = {
        "preprocess": {"eye_crop_size": [8, 8]},
        "backbone": {"params": {"bins": 90}},
    }
    cfg = load_config(tmp_path, overrides=overrides)
    hash_before = cfg.hash()

    overrides["preprocess"]["eye_crop_size"].append(99)
    overrides["backbone"]["params"]["bins"] = 28

    assert cfg.preprocess.eye_crop_size == [8, 8]
    assert cfg.backbone.params == {"bins": 90}
    assert cfg.hash() == hash_before


def test_conditions_override_replaces_the_protocol_rather_than_appending(tmp_path):
    _write(tmp_path, "collection.yaml", "conditions:\n  - key: from_file\n    seconds: 20\n")

    cfg = load_config(
        tmp_path,
        overrides={"collection": {"conditions": [{"key": "from_override", "seconds": 5}]}},
    )

    assert [c.key for c in cfg.collection.conditions] == ["from_override"]


# ==========================================================================
# config: collection protocol
# ==========================================================================


def test_conditions_are_built_into_protocol_condition_objects(cfg):
    assert cfg.collection.conditions, "shipped collection.yaml must define the protocol"
    assert all(isinstance(c, ProtocolCondition) for c in cfg.collection.conditions)
    assert len({c.key for c in cfg.collection.conditions}) == len(cfg.collection.conditions)


def test_conditions_is_not_passed_through_as_a_raw_key(tmp_path):
    """``conditions`` is stripped before ``_build`` and re-attached as objects."""
    _write(
        tmp_path,
        "collection.yaml",
        "record_fps: 25\nconditions:\n  - key: a\n    seconds: 2\n    retired: 1\n",
    )
    cfg = load_config(tmp_path)

    assert cfg.collection.record_fps == 25
    assert [c.key for c in cfg.collection.conditions] == ["a"]
    assert cfg.collection.conditions[0].seconds == 2
    assert not hasattr(cfg.collection.conditions[0], "retired")
    assert cfg.collection.conditions[0].cue == ""  # unset cue means an alternating block


def test_collection_conditions_default_is_not_shared_between_configs():
    first, second = CollectionConfig(), CollectionConfig()
    first.conditions.append(ProtocolCondition(key="x", seconds=1.0))
    assert second.conditions == []


def test_every_shipped_condition_is_either_fixed_cue_or_alternating(cfg):
    """A block with neither a cue nor a period cannot produce protocol labels."""
    for condition in cfg.collection.conditions:
        assert condition.seconds > 0
        assert bool(condition.cue) != bool(condition.alternate_period_s > 0), condition.key
        if condition.cue:
            GazeLabel.coerce(condition.cue)  # raises if a cue is not a real label


# ==========================================================================
# config: hashing and derived values
# ==========================================================================


def test_hash_is_twelve_lowercase_hex_characters(cfg):
    digest = cfg.hash()
    assert len(digest) == 12
    assert all(c in "0123456789abcdef" for c in digest)


def test_hash_is_stable_across_independent_loads_of_the_same_tree():
    assert load_config().hash() == load_config().hash()
    assert VisionConfig().hash() == VisionConfig().hash()


def test_hash_depends_on_values_not_on_object_identity():
    a = VisionConfig(temporal=TemporalConfig(window_frames=5))
    b = VisionConfig(temporal=TemporalConfig(window_frames=5))
    assert a is not b and a.temporal is not b.temporal
    assert a.hash() == b.hash()


@pytest.mark.parametrize(
    "section, key, value",
    [
        ("preprocess", "analysis_fps", 15.0),
        ("backbone", "name", "l2cs"),
        ("placement", "mode", "geometric"),
        ("calibration", "p_max_threshold", 0.9),
        ("temporal", "ema_alpha", 0.5),
        ("release_gate", "min_macro_f1", 0.9),
        ("collection", "record_fps", 60),
    ],
)
def test_hash_changes_when_any_single_section_changes(fresh_cfg, section, key, value):
    before = fresh_cfg.hash()
    setattr(getattr(fresh_cfg, section), key, value)
    assert fresh_cfg.hash() != before


def test_hash_distinguishes_every_section_so_provenance_cannot_collide():
    digests = set()
    for section, key, value in [
        ("preprocess", "analysis_fps", 15.0),
        ("backbone", "num_threads", 4),
        ("placement", "max_iter", 500),
        ("calibration", "max_iter", 500),
        ("temporal", "window_frames", 4),
        ("release_gate", "max_p95_latency_ms", 200.0),
        ("collection", "record_fps", 60),
    ]:
        cfg = VisionConfig()
        setattr(getattr(cfg, section), key, value)
        digests.add(cfg.hash())
    assert len(digests) == 7


def test_hash_notices_a_change_inside_the_nested_condition_list():
    cfg = VisionConfig()
    before = cfg.hash()
    cfg.collection.conditions.append(ProtocolCondition(key="extra", seconds=10.0))
    assert cfg.hash() != before


def test_to_dict_is_a_deep_copy_the_caller_cannot_use_to_edit_the_config(fresh_cfg):
    dumped = fresh_cfg.to_dict()
    before = fresh_cfg.hash()

    dumped["preprocess"]["eye_crop_size"].append(999)
    dumped["temporal"]["window_frames"] = -1
    dumped["collection"]["conditions"].clear()

    assert fresh_cfg.hash() == before
    assert fresh_cfg.preprocess.eye_crop_size == PreprocessConfig().eye_crop_size


@pytest.mark.parametrize(
    "fps, interval_ms", [(8.0, 125.0), (30.0, 1000.0 / 30.0), (1.0, 1000.0), (0.5, 2000.0)]
)
def test_frame_interval_is_the_reciprocal_of_the_analysis_fps(fresh_cfg, fps, interval_ms):
    fresh_cfg.preprocess.analysis_fps = fps
    assert fresh_cfg.frame_interval_ms() == pytest.approx(interval_ms)


def test_shipped_analysis_fps_leaves_the_documented_latency_budget(cfg):
    """doc 7: the p95 latency gate is derived from the frame interval."""
    assert cfg.frame_interval_ms() == pytest.approx(125.0)
    assert cfg.release_gate.max_p95_latency_ms <= cfg.frame_interval_ms()


def test_zero_analysis_fps_fails_loudly_instead_of_returning_infinity(fresh_cfg):
    """A typo'd fps must not silently become an infinite frame interval."""
    fresh_cfg.preprocess.analysis_fps = 0.0
    with pytest.raises(ZeroDivisionError):
        fresh_cfg.frame_interval_ms()


def test_frame_interval_accepts_an_integer_fps_from_yaml(fresh_cfg):
    fresh_cfg.preprocess.analysis_fps = 8  # YAML may parse it as an int
    assert fresh_cfg.frame_interval_ms() == pytest.approx(125.0)


# ==========================================================================
# config: the shipped tree
# ==========================================================================


def test_shipped_yaml_does_not_drift_from_the_dataclass_defaults(cfg):
    """The docstring promises the YAML carries overrides only.

    Everything the shipped files set must therefore equal the default, and the
    protocol condition list is the single intentional exception.
    """
    shipped, defaults = cfg.to_dict(), VisionConfig().to_dict()
    shipped_conditions = shipped["collection"].pop("conditions")
    defaults["collection"].pop("conditions")

    assert shipped == defaults
    assert shipped_conditions, "collection.yaml is the one file that adds content"


def test_layout_constants_still_point_at_the_real_tree():
    """``parents[3]`` breaks silently if config.py is ever moved."""
    assert (REPO_ROOT / "ai" / "src" / "vision" / "config.py").is_file()
    assert AI_ROOT == REPO_ROOT / "ai"
    assert CONFIG_DIR == AI_ROOT / "configs"
    assert CONFIG_DIR.is_dir()


def test_default_config_dir_is_used_when_none_is_given(cfg):
    assert cfg.hash() == load_config(CONFIG_DIR).hash()


@pytest.mark.parametrize("relative", ["ai/models/face_landmarker.task", "ai/reports"])
def test_resolve_path_anchors_a_relative_path_at_the_repo_root(relative):
    resolved = resolve_path(relative)
    assert resolved.is_absolute()
    assert resolved == REPO_ROOT / relative


def test_resolve_path_leaves_an_absolute_path_alone():
    absolute = Path(REPO_ROOT.anchor) / "opt" / "models" / "gaze.pt"
    assert resolve_path(str(absolute)) == absolute


def test_configured_landmarker_path_is_repo_relative(cfg):
    """It is resolved through ``resolve_path``, so an absolute default would pin
    the model to one machine."""
    assert not Path(cfg.preprocess.landmarker_model_path).is_absolute()
    assert resolve_path(cfg.preprocess.landmarker_model_path).name.endswith(".task")


@pytest.mark.parametrize(
    "section_cls, field_name",
    [
        (CalibrationConfig, "feature_set"),
        (PlacementConfig, "mode"),
        (PreprocessConfig, "running_mode"),
    ],
)
def test_string_valued_switches_have_a_concrete_default(section_cls, field_name):
    value = getattr(section_cls(), field_name)
    assert isinstance(value, str) and value.strip() == value and value


def test_thresholds_that_gate_the_pipeline_stay_inside_their_units(cfg):
    """Probabilities, ratios and accuracies are all 0..1; a percentage typo here
    would disable a gate rather than fail."""
    unit_interval = [
        cfg.preprocess.min_face_confidence,
        cfg.preprocess.min_face_area_ratio,
        cfg.preprocess.min_eye_openness,
        cfg.calibration.min_loo_accuracy,
        cfg.calibration.p_max_threshold,
        cfg.calibration.margin_threshold,
        cfg.placement.min_loo_accuracy,
        cfg.placement.min_sample_confidence,
        cfg.temporal.ema_alpha,
        cfg.temporal.enter_bottom_threshold,
        cfg.temporal.enter_camera_threshold,
        cfg.release_gate.min_macro_f1,
        cfg.release_gate.min_bottom_recall,
        cfg.release_gate.min_per_user_f1,
        cfg.release_gate.max_uncertain_ratio,
        cfg.release_gate.max_calibration_failure_rate,
    ]
    assert all(0.0 <= v <= 1.0 for v in unit_interval)
    # A 2-class posterior can never fall below 0.5, so a p_max gate under it
    # would never fire.
    assert cfg.calibration.p_max_threshold > 0.5
    assert cfg.calibration.class_weight == "balanced"


def test_positive_counters_and_durations_are_positive(cfg):
    for value in (
        cfg.preprocess.face_crop_size,
        cfg.preprocess.num_faces,
        cfg.temporal.window_frames,
        cfg.temporal.to_bottom_dwell_ms,
        cfg.temporal.to_camera_dwell_ms,
        cfg.temporal.uncertain_dwell_ms,
        cfg.temporal.heartbeat_ms,
        cfg.calibration.min_samples_per_class,
        cfg.placement.min_samples_per_target,
        cfg.collection.record_fps,
    ):
        assert value > 0
    assert len(cfg.preprocess.eye_crop_size) == 2
