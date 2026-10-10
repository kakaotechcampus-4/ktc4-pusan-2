"""Contracts of the reference-anchor classifier (``gaze_lab.calibration.references``).

* the soft-box density is a proper, numerically safe density that reduces to
  a Gaussian for a point;
* the regions are where the geometry says (the lens on the screen's top edge,
  the script box nested in the screen);
* gaze near an anchor decides that anchor, gaze far from all of them is OTHER,
  a turned head is OTHER, an unusable frame abstains as in the logistic path;
* the quality ladder can reach every reason, most specific first;
* the factory and the version stamp follow ``calibration.method``.

Calibration samples are drawn around hand-placed anchors (degrees) with a fixed
seed; each test names the one property it exercises.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from gaze_lab.calibration.factory import CALIBRATION_METHODS, fit_gaze_classifier, normalise_method
from gaze_lab.calibration.references import (
    ReferenceAnchorClassifier,
    build_reference_model,
    decide_label,
    robust_sigma,
    soft_box_log_density,
)
from gaze_lab.config import CalibrationConfig
from gaze_lab.schemas import (
    INVERTED_PITCH_HINT_PREFIX,
    STATE_CLASSES,
    CalibrationFailReason,
    CalibrationSample,
    FrameObservation,
    GazeVector,
    HeadPose,
)

CAMERA_DEG = (0.5, -1.0)
SCREEN_DEG = (0.8, -10.5)
BOTTOM_DEG = (0.6, -18.0)


def _sample(label, yaw_deg, pitch_deg, head_yaw_deg=0.0, head_pitch_deg=0.0):
    return CalibrationSample(
        label=label,
        gaze=GazeVector(math.radians(yaw_deg), math.radians(pitch_deg), 0.9),
        head_pose=HeadPose(yaw=math.radians(head_yaw_deg), pitch=math.radians(head_pitch_deg)),
    )


def _cloud(label, centre, n=16, sd=0.6, seed=0):
    rng = np.random.default_rng(seed)
    return [_sample(label, centre[0] + rng.normal(0, sd), centre[1] + rng.normal(0, sd)) for _ in range(n)]


def _calibration(screen=True, n=16, sd=0.6, camera=CAMERA_DEG, screen_at=SCREEN_DEG, bottom=BOTTOM_DEG):
    samples = _cloud("CAMERA", camera, n, sd, 1) + _cloud("BOTTOM", bottom, n, sd, 2)
    if screen:
        samples += _cloud("SCREEN", screen_at, n, sd, 3)
    return samples


@pytest.fixture
def ccfg() -> CalibrationConfig:
    return CalibrationConfig(method="reference")


@pytest.fixture
def fitted(ccfg):
    clf, quality = ReferenceAnchorClassifier.fit(_calibration(), ccfg)
    assert quality.ok, quality
    return clf


def _obs(yaw_deg=0.0, pitch_deg=0.0, valid=True, reason=None, head_yaw_deg=0.0, head_pitch_deg=0.0):
    return FrameObservation(
        frame_id=1, t_ms=125, face_confidence=1.0, face_valid=valid, invalid_reason=reason,
        head_pose=HeadPose(yaw=math.radians(head_yaw_deg), pitch=math.radians(head_pitch_deg)),
    )


def _gaze(yaw_deg, pitch_deg):
    return GazeVector(math.radians(yaw_deg), math.radians(pitch_deg), 0.9)


# ==========================================================================
# density
# ==========================================================================


@pytest.mark.parametrize("lo, hi, s", [(0.0, 0.0, 1.5), (-4.0, 6.0, 1.5), (-0.001, 0.001, 2.0), (2.0, 30.0, 0.5)])
def test_soft_box_is_a_proper_density(lo, hi, s):
    xs = np.linspace(lo - 12 * s, hi + 12 * s, 40001)
    density = np.exp([soft_box_log_density(x, lo, hi, s) for x in xs])
    assert np.trapezoid(density, xs) == pytest.approx(1.0, abs=1e-4)


def test_a_point_box_is_the_gaussian_pdf():
    for x in (-3.0, 0.0, 1.7):
        expected = -0.5 * (x / 2.0) ** 2 - math.log(2.0) - 0.5 * math.log(2 * math.pi)
        assert soft_box_log_density(x, 0.0, 0.0, 2.0) == pytest.approx(expected)


@pytest.mark.parametrize("x", [-200.0, -80.0, 80.0, 200.0])
def test_far_tails_stay_finite_and_fall_monotonically(x):
    near = soft_box_log_density(math.copysign(30.0, x), -5.0, 5.0, 1.5)
    far = soft_box_log_density(x, -5.0, 5.0, 1.5)
    assert math.isfinite(far)
    assert far < near


def test_reversed_box_bounds_are_the_same_box():
    assert soft_box_log_density(1.0, 5.0, -5.0, 1.0) == soft_box_log_density(1.0, -5.0, 5.0, 1.0)


def test_robust_sigma_is_floored_and_scaled(ccfg):
    assert robust_sigma(np.zeros(20), ccfg) == ccfg.sigma_min_deg
    wide = np.asarray([-4.0, 4.0] * 10)
    assert robust_sigma(wide, ccfg) == pytest.approx(ccfg.sigma_scale * 1.4826 * 4.0)


# ==========================================================================
# geometry
# ==========================================================================


def test_screen_box_puts_the_lens_on_its_top_edge(ccfg):
    model = build_reference_model(_calibration(sd=0.0), ccfg)
    screen = model.boxes["SCREEN"]
    assert screen.pitch_hi == pytest.approx(CAMERA_DEG[1])
    half_h = CAMERA_DEG[1] - SCREEN_DEG[1]
    assert screen.pitch_lo == pytest.approx(SCREEN_DEG[1] - half_h)
    # The width follows the aspect, but never wider than the cap each side.
    assert (screen.yaw_hi - screen.yaw_lo) / 2 == pytest.approx(
        min(half_h * ccfg.screen_aspect, ccfg.screen_max_halfwidth_deg))


def test_a_head_lifted_far_for_the_lens_does_not_widen_the_screen_past_the_cap(ccfg):
    # 9.5 deg between lens and screen centre: the aspect alone would make the
    # screen ~17 deg wide each side, and a clear turn aside would never be OTHER.
    model = build_reference_model(_calibration(sd=0.0), ccfg)
    screen = model.boxes["SCREEN"]
    assert (CAMERA_DEG[1] - SCREEN_DEG[1]) * ccfg.screen_aspect > ccfg.screen_max_halfwidth_deg
    assert (screen.yaw_hi - screen.yaw_lo) / 2 == pytest.approx(ccfg.screen_max_halfwidth_deg)
    narrow = build_reference_model(_calibration(sd=0.0), dataclasses.replace(ccfg, screen_max_halfwidth_deg=30.0))
    wide_half = (narrow.boxes["SCREEN"].yaw_hi - narrow.boxes["SCREEN"].yaw_lo) / 2
    assert wide_half == pytest.approx((CAMERA_DEG[1] - SCREEN_DEG[1]) * ccfg.screen_aspect)


def test_script_box_is_a_fraction_of_the_screen_around_the_bottom_anchor(ccfg):
    model = build_reference_model(_calibration(sd=0.0), ccfg)
    screen, script = model.boxes["SCREEN"], model.boxes["BOTTOM"]
    assert (script.yaw_hi - script.yaw_lo) == pytest.approx(ccfg.script_width_fraction * (screen.yaw_hi - screen.yaw_lo))
    assert (script.pitch_lo + script.pitch_hi) / 2 == pytest.approx(BOTTOM_DEG[1])


def test_two_cue_calibration_has_no_screen_class(ccfg):
    model = build_reference_model(_calibration(screen=False), ccfg)
    assert model.classes == ("CAMERA", "BOTTOM", "OTHER")
    assert "SCREEN" not in model.boxes


def test_camera_only_calibration_builds_nothing(ccfg):
    assert build_reference_model(_cloud("CAMERA", CAMERA_DEG), ccfg) is None
    clf, quality = ReferenceAnchorClassifier.fit(_cloud("CAMERA", CAMERA_DEG), ccfg)
    assert not clf.is_fitted
    assert quality.reason == CalibrationFailReason.NOT_ENOUGH_SAMPLES.value


def test_a_zero_prior_switches_a_class_off(ccfg):
    model = build_reference_model(_calibration(), dataclasses.replace(ccfg, prior_other=0.0))
    assert "OTHER" not in model.classes


def test_classes_come_out_in_state_order(fitted):
    assert fitted.classes == STATE_CLASSES


# ==========================================================================
# decisions
# ==========================================================================


@pytest.mark.parametrize(
    "gaze, expected",
    [
        (CAMERA_DEG, "CAMERA"),
        (SCREEN_DEG, "SCREEN"),
        (BOTTOM_DEG, "BOTTOM"),
        ((SCREEN_DEG[0] - 12.0, SCREEN_DEG[1]), "SCREEN"),  # left part of the screen
        ((BOTTOM_DEG[0] + 6.0, BOTTOM_DEG[1]), "BOTTOM"),  # reading along a script line
        ((40.0, -5.0), "OTHER"),  # out of the window
        ((0.5, 25.0), "OTHER"),  # ceiling
    ],
)
def test_gaze_is_decided_by_the_region_it_falls_in(fitted, gaze, expected):
    decision = fitted.decide(_obs(), _gaze(*gaze))
    assert decision.label == expected
    assert decision.probs[expected] == pytest.approx(decision.p_max)
    assert sum(decision.probs.values()) == pytest.approx(1.0)
    assert decision.p_camera == decision.probs["CAMERA"]
    assert decision.p_bottom == decision.probs["BOTTOM"]


def test_the_lens_to_screen_line_reads_camera_then_abstains_then_screen(fitted):
    """Walking down from the lens: CAMERA, a short abstention band, SCREEN.

    The band sits where the eye-contact zone ends, i.e. at the capture radius.
    """
    yaw = (CAMERA_DEG[0] + SCREEN_DEG[0]) / 2
    pitches = np.arange(CAMERA_DEG[1], SCREEN_DEG[1], -0.05)
    labels = [fitted.decide(_obs(), _gaze(yaw, p)).label for p in pitches]

    collapsed = [labels[0]] + [b for a, b in zip(labels, labels[1:]) if b != a]
    assert collapsed == ["CAMERA", "UNCERTAIN", "SCREEN"]
    band = [p for p, label in zip(pitches, labels) if label == "UNCERTAIN"]
    centre_of_band = CAMERA_DEG[1] - float(np.mean(band))
    assert centre_of_band == pytest.approx(fitted.quality.camera_capture_radius_deg, abs=0.75)
    reasons = {fitted.decide(_obs(), _gaze(yaw, p)).uncertain_reason for p in band}
    assert reasons <= {"LOW_CONFIDENCE", "LOW_MARGIN"}


def test_dropping_the_chin_to_read_still_reads_as_the_script(fitted):
    """Head-fixed calibration plus combined gaze: no head-pose shortcut exists."""
    decision = fitted.decide(_obs(head_pitch_deg=-12.0), _gaze(*BOTTOM_DEG))
    assert decision.label == "BOTTOM"


def test_a_head_turned_far_away_is_other_whatever_the_eyes_read(fitted):
    decision = fitted.decide(_obs(head_yaw_deg=50.0), _gaze(*CAMERA_DEG))
    assert decision.label == "OTHER"
    assert decision.probs["OTHER"] == 1.0


def test_head_away_without_an_other_class_abstains(ccfg):
    clf, _ = ReferenceAnchorClassifier.fit(_calibration(), dataclasses.replace(ccfg, prior_other=0.0))
    decision = clf.decide(_obs(head_yaw_deg=50.0), _gaze(*CAMERA_DEG))
    assert decision.label == "UNCERTAIN" and decision.uncertain_reason == "HEAD_AWAY"


@pytest.mark.parametrize(
    "valid, reason, gaze, expected_reason",
    [
        (False, "EYES_CLOSED", (0.0, 0.0), "EYES_CLOSED"),
        (False, None, (0.0, 0.0), "NO_FACE"),
        (True, None, None, "BACKBONE_FAILED"),
        (True, None, (math.nan, 0.0), "BACKBONE_FAILED"),
    ],
)
def test_unusable_frames_abstain_like_the_logistic_path(fitted, valid, reason, gaze, expected_reason):
    decision = fitted.decide(_obs(valid=valid, reason=reason), None if gaze is None else _gaze(*gaze))
    assert decision.label == "UNCERTAIN"
    assert decision.face_valid is False
    assert decision.uncertain_reason == expected_reason
    assert (decision.p_camera, decision.p_bottom) == (0.5, 0.5)


def test_exact_ties_go_to_the_earlier_state(ccfg):
    cfg = dataclasses.replace(ccfg, p_max_threshold=0.0, margin_threshold=0.0)
    assert decide_label({"CAMERA": 0.5, "BOTTOM": 0.5}, cfg) == ("CAMERA", None)
    assert decide_label({"SCREEN": 0.4, "BOTTOM": 0.4, "OTHER": 0.2}, cfg) == ("SCREEN", None)


def test_a_valid_frame_on_an_unfitted_model_raises(ccfg):
    clf = ReferenceAnchorClassifier(None, ccfg)
    with pytest.raises(RuntimeError, match="not fitted"):
        clf.decide(_obs(), _gaze(0.0, 0.0))


# ==========================================================================
# re-anchor
# ==========================================================================


def test_with_offset_moves_every_anchor_and_region_but_keeps_their_sizes(fitted):
    moved = fitted.with_offset(3.0, -2.0)
    for cue, (y, p) in fitted.model.anchors.items():
        assert moved.model.anchors[cue] == pytest.approx((y + 3.0, p - 2.0))
    for cls, box in fitted.model.boxes.items():
        assert moved.model.boxes[cls].area == pytest.approx(box.area)
    shifted_gaze = (BOTTOM_DEG[0] + 3.0, BOTTOM_DEG[1] - 2.0)
    assert moved.decide(_obs(), _gaze(*shifted_gaze)).label == "BOTTOM"
    assert fitted.model.anchors["CAMERA"] == pytest.approx(CAMERA_DEG, abs=1.0)  # original untouched


def test_with_head_baseline_rebases_the_head_away_rule(fitted):
    rebased = fitted.with_head_baseline(HeadPose(yaw=math.radians(40.0)))
    assert rebased.decide(_obs(head_yaw_deg=45.0), _gaze(*CAMERA_DEG)).label == "CAMERA"
    assert fitted.decide(_obs(head_yaw_deg=45.0), _gaze(*CAMERA_DEG)).label == "OTHER"


# ==========================================================================
# quality ladder
# ==========================================================================


def test_a_clean_three_cue_calibration_passes_with_its_diagnostics(fitted):
    quality = fitted.quality
    assert quality.ok and quality.method == "reference"
    assert (quality.n_camera, quality.n_screen, quality.n_bottom) == (16, 16, 16)
    assert set(quality.anchors_deg) == {"CAMERA", "SCREEN", "BOTTOM"}
    assert set(quality.pair_separation) == {"CAMERA-BOTTOM", "CAMERA-SCREEN", "SCREEN-BOTTOM"}
    assert quality.loo_accuracy >= 0.95
    assert 0.0 < quality.camera_capture_radius_deg < 9.5  # inside the lens-to-centre gap


def test_capture_radius_matches_its_closed_form(fitted):
    model = fitted.model
    s = math.sqrt(model.sigma[0] * model.sigma[1])
    log_peak = model.log_priors["CAMERA"] - math.log(2 * math.pi * model.sigma[0] * model.sigma[1])
    log_screen = model.log_priors["SCREEN"] - math.log(model.boxes["SCREEN"].area)
    assert model.camera_capture_radius_deg() == pytest.approx(s * math.sqrt(2 * (log_peak - log_screen)))


def test_not_enough_samples(ccfg):
    _, quality = ReferenceAnchorClassifier.fit(_calibration(n=6), ccfg)
    assert quality.reason == CalibrationFailReason.NOT_ENOUGH_SAMPLES.value


def test_a_half_collected_screen_cue_fails_rather_than_silently_dropping(ccfg):
    samples = _calibration(screen=False) + _cloud("SCREEN", SCREEN_DEG, n=4)
    _, quality = ReferenceAnchorClassifier.fit(samples, ccfg)
    assert quality.reason == CalibrationFailReason.NOT_ENOUGH_SAMPLES.value
    assert "SCREEN" in quality.hint


def test_a_gaze_that_never_moved_is_degenerate(ccfg):
    samples = [_sample(c, 1.0, -3.0) for c in ("CAMERA", "SCREEN", "BOTTOM") for _ in range(16)]
    _, quality = ReferenceAnchorClassifier.fit(samples, ccfg)
    assert quality.reason == CalibrationFailReason.DEGENERATE_FEATURES.value


def test_lens_and_script_too_close_is_not_separable(ccfg):
    _, quality = ReferenceAnchorClassifier.fit(_calibration(screen=False, bottom=(0.5, -4.0)), ccfg)
    assert quality.reason == CalibrationFailReason.CLASS_NOT_SEPARABLE.value


def test_screen_on_top_of_the_lens_names_the_pair(ccfg):
    cfg = dataclasses.replace(ccfg, merge_inseparable_screen=False)
    _, quality = ReferenceAnchorClassifier.fit(_calibration(screen_at=(0.6, -3.5)), cfg)
    assert quality.reason == CalibrationFailReason.CENTROIDS_TOO_CLOSE.value
    assert "CAMERA-SCREEN" in quality.hint


def test_a_diluted_script_region_is_ambiguous(ccfg):
    cfg = dataclasses.replace(ccfg, script_width_fraction=1.0, script_height_fraction=1.0, prior_bottom=0.05,
                              merge_inseparable_screen=False)
    _, quality = ReferenceAnchorClassifier.fit(_calibration(), cfg)
    assert quality.reason == CalibrationFailReason.ANCHOR_AMBIGUOUS.value
    assert "BOTTOM" in quality.hint


def test_overlapping_clouds_fail_leave_one_out(ccfg):
    cfg = dataclasses.replace(ccfg, min_anchor_separation=0.0)
    samples = _calibration(screen=False, sd=6.0, bottom=(0.5, -9.0))
    _, quality = ReferenceAnchorClassifier.fit(samples, cfg)
    assert quality.reason in (
        CalibrationFailReason.LOW_LOO_ACCURACY.value,
        CalibrationFailReason.ANCHOR_AMBIGUOUS.value,
    )
    assert quality.loo_accuracy < cfg.min_loo_accuracy or quality.reason == "ANCHOR_AMBIGUOUS"


def test_an_inverted_target_order_warns_without_failing(ccfg):
    samples = _calibration(camera=(0.5, -18.0), screen_at=(0.8, -10.5), bottom=(0.6, -1.0))
    _, quality = ReferenceAnchorClassifier.fit(samples, ccfg)
    assert quality.hint is not None and INVERTED_PITCH_HINT_PREFIX in quality.hint
    assert any(INVERTED_PITCH_HINT_PREFIX in w for w in quality.warnings)


# ==========================================================================
# factory and version stamp
# ==========================================================================


def test_factory_follows_the_configured_method(ccfg):
    from gaze_lab.calibration.classifier import PerUserGazeClassifier

    ref, _ = fit_gaze_classifier(_calibration(), ccfg)
    lr, quality = fit_gaze_classifier(_calibration(), dataclasses.replace(ccfg, method=" Logistic "))
    assert isinstance(ref, ReferenceAnchorClassifier) and ref.method == "reference"
    assert isinstance(lr, PerUserGazeClassifier) and lr.method == "logistic"
    assert lr.classes == ("CAMERA", "BOTTOM")
    assert quality.method == "logistic"


def test_an_unknown_method_fails_instead_of_defaulting(ccfg):
    with pytest.raises(ValueError, match="unknown calibration method"):
        fit_gaze_classifier(_calibration(), dataclasses.replace(ccfg, method="svm"))
    assert set(CALIBRATION_METHODS) == {"reference", "logistic"}
    assert normalise_method("REFERENCE") == "reference"


@pytest.mark.parametrize("method, stamp", [("reference", "reference_anchor_v1"), ("logistic", "per_user_lr_v1")])
def test_the_version_stamp_names_the_method_that_ran(fresh_cfg, method, stamp):
    from gaze_lab.runtime.version import current_ai_version

    fresh_cfg.calibration.method = method
    assert current_ai_version(fresh_cfg, "mediapipe_geom").gaze_classifier == stamp


def test_decide_is_fast_enough_for_the_frame_budget(fitted):
    import time

    obs, gaze = _obs(), _gaze(3.0, -12.0)
    started = time.perf_counter()
    for _ in range(500):
        fitted.decide(obs, gaze)
    per_call_ms = (time.perf_counter() - started) * 1000.0 / 500
    assert per_call_ms < 2.0
