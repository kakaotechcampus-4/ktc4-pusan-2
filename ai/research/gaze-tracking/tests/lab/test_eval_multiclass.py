"""Evaluation with a classifier that decides more classes than the truth has.

The recorded ground truth is CAMERA / BOTTOM only; the shipped reference-anchor
classifier also answers SCREEN and OTHER.  These tests pin how the harness
counts those answers (decided and wrong, never abstentions), that the
vectorised rule replay agrees with the classifier frame for frame, and that
``gaze_eval.run_evaluation`` runs end to end on a small synthetic table.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from gaze_lab.evaluation import gaze_eval, metrics, sanity

from gaze_lab.calibration.references import ReferenceAnchorClassifier
from gaze_lab.data.manifest import make_sample_id
from gaze_lab.config import CalibrationConfig
from gaze_lab.schemas import CalibrationSample, FrameObservation, GazeVector, HeadPose

CAMERA, BOTTOM, SCREEN, OTHER, UNCERTAIN = "CAMERA", "BOTTOM", "SCREEN", "OTHER", "UNCERTAIN"
ANCHORS = {CAMERA: (0.5, -1.0), SCREEN: (0.8, -10.5), BOTTOM: (0.6, -18.0)}


# ==========================================================================
# metrics
# ==========================================================================


def test_off_target_answers_are_decided_misses_not_abstentions():
    scores = metrics.frame_metrics(
        [CAMERA, CAMERA, CAMERA, BOTTOM, BOTTOM],
        [CAMERA, SCREEN, UNCERTAIN, BOTTOM, OTHER],
    )
    assert scores["n_decided"] == 4
    assert scores["n_uncertain"] == 1
    assert scores["n_off_target"] == 2
    assert scores["off_target_ratio"] == pytest.approx(2 / 5)
    assert scores["accuracy"] == pytest.approx(2 / 4)
    assert scores["camera_recall"] == pytest.approx(1 / 2)  # the SCREEN frame is a miss
    assert scores["per_class"][CAMERA]["precision"] == 1.0  # never a false positive
    assert scores["confusion"][CAMERA] == {CAMERA: 1, BOTTOM: 0, UNCERTAIN: 1}
    assert scores["confusion_off_target"] == {CAMERA: {SCREEN: 1, OTHER: 0}, BOTTOM: {SCREEN: 0, OTHER: 1}}


def test_an_unknown_prediction_still_fails_loudly():
    with pytest.raises(ValueError, match="unknown predicted label"):
        metrics.frame_metrics([CAMERA], ["SIDEWAYS"])


def test_off_target_answers_have_no_uncertain_reason():
    scores = metrics.frame_metrics(
        [CAMERA, CAMERA], [SCREEN, UNCERTAIN], uncertain_reasons=["LOW_CONFIDENCE", "NO_FACE"]
    )
    assert scores["uncertain_by_reason"] == {"NO_FACE": 1}


def test_a_script_glance_reported_as_screen_is_a_missed_segment():
    stream = [(0, BOTTOM), (100, SCREEN), (1000, CAMERA)]
    segments = [{"start_ms": 0, "end_ms": 1000, "label": BOTTOM}]
    records = metrics.segment_predictions(stream, segments)
    assert records[0]["pred_label"] == SCREEN
    assert records[0]["covered_ms"] == {CAMERA: 0.0, BOTTOM: 100.0, UNCERTAIN: 0.0}
    assert records[0]["off_target_ms"] == {SCREEN: 900.0, OTHER: 0.0}
    scored = metrics.segment_metrics_from_predictions(records)
    assert scored["bottom_recall"] == 0.0
    assert scored["time_weighted"]["off_target_ms"] == pytest.approx(900.0)
    assert scored["time_weighted"]["total_ms"] == pytest.approx(1000.0)


def test_sanity_does_not_triage_off_target_answers_as_abstentions():
    df = pd.DataFrame(
        {
            "pred_label": [SCREEN, OTHER, UNCERTAIN, CAMERA],
            "uncertain_reason": [None, None, "LOW_MARGIN", None],
            "invalid_reason": [None, None, None, None],
        }
    )
    report = sanity.uncertain_reason_report(df)
    assert report["n"].sum() == 1
    assert report["reason"].tolist() == ["LOW_MARGIN"]


# ==========================================================================
# rule replay == classifier
# ==========================================================================


def _samples(seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for cue, (yaw, pitch) in ANCHORS.items():
        for _ in range(16):
            out.append(
                CalibrationSample(
                    label=cue,
                    gaze=GazeVector(math.radians(yaw + rng.normal(0, 0.6)), math.radians(pitch + rng.normal(0, 0.6)), 0.9),
                    head_pose=HeadPose(),
                )
            )
    return out


def test_the_vectorised_rule_replays_the_reference_classifier_frame_for_frame():
    cfg = CalibrationConfig(method="reference")
    clf, quality = ReferenceAnchorClassifier.fit(_samples(), cfg)
    assert quality.ok
    obs = FrameObservation(frame_id=0, t_ms=0, face_confidence=1.0, face_valid=True)
    decisions = [
        clf.decide(obs, GazeVector(math.radians(yaw), math.radians(pitch), 0.9))
        for yaw in np.linspace(-25, 25, 21)
        for pitch in np.linspace(-30, 15, 31)
    ]
    labels = {d.label for d in decisions}
    assert {CAMERA, SCREEN, BOTTOM, OTHER, UNCERTAIN} <= labels  # the grid hits every outcome
    replayed = gaze_eval.apply_uncertain_rule(
        [d.p_camera for d in decisions],
        [d.p_bottom for d in decisions],
        [d.face_valid for d in decisions],
        cfg.p_max_threshold,
        cfg.margin_threshold,
        p_screen=[d.probs.get(SCREEN) for d in decisions],
        p_other=[d.probs.get(OTHER) for d in decisions],
    )
    assert replayed.tolist() == [d.label for d in decisions]


def test_the_two_class_replay_is_unchanged():
    out = gaze_eval.apply_uncertain_rule([0.9, 0.5, 0.2, math.nan], [0.1, 0.5, 0.8, 0.5],
                                         [True, True, True, True], 0.7, 0.2)
    assert out.tolist() == [CAMERA, UNCERTAIN, BOTTOM, UNCERTAIN]


# ==========================================================================
# gaze_eval end to end
# ==========================================================================


def _table(n_participants=2, seed=0) -> pd.DataFrame:
    """Calibration block (CAMERA / SCREEN / BOTTOM) then labelled evaluation frames."""
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_participants):
        pid = f"P{p + 1:02d}"
        t = 0
        def add(label, condition, centre):
            nonlocal t
            yaw, pitch = centre
            rows.append(
                {
                    "participant_id": pid, "session_id": "S01", "frame_id": t // 125, "t_ms": t,
                    "sample_id": make_sample_id(pid, "S01", t // 125),
                    "label": label, "condition": condition, "backbone": "mediapipe_geom",
                    "gaze_yaw": math.radians(yaw + rng.normal(0, 0.6)),
                    "gaze_pitch": math.radians(pitch + rng.normal(0, 0.6)),
                    "gaze_confidence": 0.9, "face_valid": True, "face_confidence": 1.0,
                    "head_yaw": 0.0, "head_pitch": 0.0, "head_roll": 0.0,
                    "invalid_reason": None, "latency_ms": 35.0,
                }
            )
            t += 125
        for cue in (CAMERA, SCREEN, BOTTOM):
            for _ in range(20):
                add(cue, f"calib_{cue.lower()}", ANCHORS[cue])
        for block in range(4):
            for label in (CAMERA, BOTTOM):
                for _ in range(24):
                    add(label, f"eval_{block}", ANCHORS[label])
    return pd.DataFrame(rows)


def test_gaze_eval_runs_end_to_end_with_the_reference_classifier(fresh_cfg):
    assert fresh_cfg.calibration.method == "reference"
    report = gaze_eval.run_evaluation(_table(), fresh_cfg)
    assert report["predictions"]["decision_rule_consistent"] is True
    assert report["config"]["method"] == "reference"
    assert report["frame"]["accuracy"] > 0.9
    assert report["frame"]["n_off_target"] < 0.1 * report["frame"]["n_labelled"]
    assert report["calibration"]["failure_rate"] == 0.0


def test_the_threshold_sweep_re_decides_over_every_class(fresh_cfg):
    from gaze_lab.evaluation import threshold_sweep

    scored = threshold_sweep.cached_probabilities(_table(), fresh_cfg)
    assert scored["p_screen"].notna().all()
    grid = threshold_sweep.sweep(scored, [0.5, fresh_cfg.calibration.p_max_threshold, 0.95], [0.0, 0.2])
    shipped = grid[
        (grid["p_max_threshold"] == fresh_cfg.calibration.p_max_threshold)
        & (grid["margin_threshold"] == fresh_cfg.calibration.margin_threshold)
    ].iloc[0]
    reference = metrics.frame_metrics(scored["label"], scored["pred_label"])
    assert shipped["macro_f1"] == pytest.approx(reference["macro_f1"])
    # A stricter confidence floor can only abstain more.
    assert grid.sort_values("p_max_threshold")["uncertain_ratio"].is_monotonic_increasing


def test_gaze_eval_still_runs_the_logistic_ablation(fresh_cfg):
    fresh_cfg.calibration.method = "logistic"
    report = gaze_eval.run_evaluation(_table(), fresh_cfg)
    assert report["predictions"]["decision_rule_consistent"] is True
    assert report["config"]["method"] == "logistic"
