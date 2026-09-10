"""Contract tests for the per-user calibration subsystem (doc 5-1 .. 5-4).

What this file protects, in the three modules it covers:

``calibration.features``
    the identity of the three ablation feature sets (names, length and ORDER),
    and the doc 5-1 leakage rule -- the CAMERA/BOTTOM anchors are frozen inside
    ``fit`` and nothing short of an explicit refit may move them, which is why
    the accessors hand out copies and why persistence stores the anchors
    verbatim instead of re-deriving them.

``calibration.quality``
    that every doc 5-2 retry reason stays reachable with the SHIPPED
    thresholds, that the reasons are emitted in the documented root-cause
    order, that unusable rows are dropped before anything is counted, and that
    the pitch-ordering check stays ADVISORY: it may only prefix
    ``INVERTED_PITCH_HINT_PREFIX`` onto the hint, never set ``reason`` and never
    flip ``status``.

``calibration.classifier``
    the documented label trap -- sklearn sorts the classes, so the fitted
    pipeline's ``predict_proba`` column 0 is BOTTOM, not CAMERA, and the class
    positions must be read off the estimator -- plus the doc 5-4 branch order
    (invalid frame -> confidence floor -> margin floor -> label), the fact that
    the LOW_MARGIN branch is unreachable at the shipped thresholds, and the
    save/load guards.

Every input here is hand-built to exercise one specific code path.  No accuracy
or F1 number is asserted anywhere: there is no recorded dataset in this repo and
a score measured on invented gaze angles would mean nothing.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import joblib
import numpy as np
import pytest

from vision.calibration.classifier import (
    SCHEMA_VERSION,
    UNCERTAIN_LOW_CONFIDENCE,
    UNCERTAIN_LOW_MARGIN,
    PerUserGazeClassifier,
    _class_indices,
)
from vision.calibration.features import (
    CENTROID_FEATURE_SETS,
    FEATURE_SETS,
    CalibrationFeatureExtractor,
    normalise_feature_set,
)
from vision.calibration.quality import (
    assess_calibration,
    build_calibration_pipeline,
    leave_one_out_accuracy,
    split_by_label,
)
from vision.schemas import (
    DECISION_CLASSES,
    INVERTED_PITCH_HINT_PREFIX,
    CalibrationFailReason,
    CalibrationSample,
    CalibrationStatus,
    FrameObservation,
    GazeState,
    GazeVector,
    HeadPose,
    InvalidReason,
)

# --------------------------------------------------------------------------
# Hand-built calibration inputs
#
# Twelve symmetric offsets, mean exactly zero, so every cluster centroid below
# is an exact hand-computable number and nothing depends on an RNG.
# --------------------------------------------------------------------------

_SPREAD = tuple((-11 + 2 * k) / 11.0 for k in range(12))  # -1.0 ... +1.0


def _sample(label, gaze_yaw, gaze_pitch, head=(0.0, 0.0, 0.0), frame_id=0, confidence=0.9):
    return CalibrationSample(
        label=label,
        gaze=GazeVector(
            gaze_yaw=float(gaze_yaw),
            gaze_pitch=float(gaze_pitch),
            confidence=confidence,
            backbone="unit-test",
        ),
        head_pose=HeadPose(yaw=float(head[0]), pitch=float(head[1]), roll=float(head[2])),
        t_ms=frame_id * 125,
        frame_id=frame_id,
    )


def _rot(k, shift):
    return _SPREAD[(k + shift) % len(_SPREAD)]


def _separable_calibration(n=12):
    """Two clean clusters obeying the schemas.py rule: BOTTOM pitch < CAMERA pitch."""
    out = []
    for k in range(n):
        out.append(
            _sample(
                "CAMERA",
                0.02 + 0.010 * _rot(k, 3),
                0.02 + 0.030 * _rot(k, 0),
                head=(0.01 * _rot(k, 1), 0.02 * _rot(k, 2), 0.01 * _rot(k, 4)),
                frame_id=k,
            )
        )
    for k in range(n):
        out.append(
            _sample(
                "BOTTOM",
                -0.01 + 0.010 * _rot(k, 7),
                -0.33 + 0.030 * _rot(k, 5),
                head=(0.01 * _rot(k, 5), -0.05 + 0.02 * _rot(k, 3), 0.01 * _rot(k, 6)),
                frame_id=100 + k,
            )
        )
    return out


def _pitch_overlap_calibration(gap, spread=0.20):
    """Both classes drawn from the same pitch spread, offset by ``gap``.

    ``gap`` alone walks the doc 5-2 ladder: small gaps drown in the spread
    (CLASS_NOT_SEPARABLE), a middling gap is geometrically fine but still
    misclassifies held-out frames (LOW_LOO_ACCURACY).  Head pose is held at
    zero so the pitch axis alone drives the geometry.
    """
    out = []
    for k in range(12):
        out.append(
            _sample("CAMERA", 0.3 * spread * _rot(k, 3), gap / 2 + spread * _rot(k, 0), frame_id=k)
        )
    for k in range(12):
        out.append(
            _sample(
                "BOTTOM", 0.3 * spread * _rot(k, 7), -gap / 2 + spread * _rot(k, 5), frame_id=100 + k
            )
        )
    return out


def _frozen_gaze_calibration(head_gap=True):
    """A dead backbone: the gaze pair never moves, the head still does."""
    out = []
    for k in range(12):
        out.append(
            _sample(
                "CAMERA",
                0.10,
                -0.05,
                head=(0.02 * _rot(k, 0), 0.04 * _rot(k, 2), 0.03 * _rot(k, 4)),
                frame_id=k,
            )
        )
    shift = (0.20, -0.30) if head_gap else (0.0, 0.0)
    for k in range(12):
        out.append(
            _sample(
                "BOTTOM",
                0.10,
                -0.05,
                head=(
                    shift[0] + 0.02 * _rot(k, 0),
                    shift[1] + 0.04 * _rot(k, 2),
                    0.03 * _rot(k, 4),
                ),
                frame_id=100 + k,
            )
        )
    return out


def _orthogonal_scatter_calibration(offset=0.02):
    """Within-class scatter along ``yaw == pitch``; the class axis is ``yaw == -pitch``.

    Standardisation divides each column by its total spread, which is dominated
    by the shared drift, so the two centroids end up very close -- while the
    Fisher ratio along the between-centroid axis is enormous, because the drift
    contributes nothing in that direction.  This is the geometry
    CENTROIDS_TOO_CLOSE exists to catch, and it keeps the schemas.py pitch
    ordering (CAMERA above BOTTOM).
    """
    out = []
    for k in range(12):
        drift = 0.05 * (k - 5.5)
        out.append(_sample("CAMERA", drift - offset, drift + offset, frame_id=k))
    for k in range(12):
        drift = 0.05 * (k - 5.5)
        out.append(_sample("BOTTOM", drift + offset, drift - offset, frame_id=100 + k))
    return out


def _observation(frame_id=7, t_ms=875, face_valid=True, invalid_reason=None):
    return FrameObservation(
        frame_id=frame_id,
        t_ms=t_ms,
        face_confidence=0.93 if face_valid else 0.11,
        face_valid=face_valid,
        head_pose=HeadPose(),
        invalid_reason=invalid_reason,
    )


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def calib_cfg(cfg):
    """The shipped doc 5 calibration section (read-only; use ``replace`` to vary)."""
    return cfg.calibration


@pytest.fixture(scope="module")
def fitted(calib_cfg):
    """A real fitted model plus its quality report, built once for the module."""
    classifier, quality = PerUserGazeClassifier.fit(_separable_calibration(), calib_cfg)
    assert classifier.is_fitted and quality.ok  # guards the rest of the module
    return classifier


def _rule_only(cfg, p_camera, p_bottom):
    """A classifier whose probabilities are pinned, to isolate the doc 5-4 rule."""
    classifier = PerUserGazeClassifier(CalibrationFeatureExtractor(cfg.feature_set), None, cfg)
    classifier.predict_proba = lambda gaze, head: (p_camera, p_bottom)
    return classifier


# ==========================================================================
# features.py -- feature set identity
# ==========================================================================


@pytest.mark.parametrize(
    "key, expected",
    [
        ("A", ("gaze_yaw", "gaze_pitch")),
        ("B", ("gaze_yaw", "gaze_pitch", "head_yaw", "head_pitch", "head_roll")),
        (
            "C",
            (
                "gaze_yaw",
                "gaze_pitch",
                "head_yaw",
                "head_pitch",
                "head_roll",
                "gaze_yaw_minus_camera",
                "gaze_pitch_minus_camera",
                "gaze_yaw_minus_bottom",
                "gaze_pitch_minus_bottom",
            ),
        ),
    ],
)
def test_feature_set_columns_are_exact_names_in_a_fixed_order(key, expected):
    # The persisted model compares these names on load, and Experiment 2 reports
    # per-column coefficients, so both the spelling and the order are contract.
    assert FEATURE_SETS[key] == expected
    assert CalibrationFeatureExtractor(key).feature_names() == list(expected)
    assert CalibrationFeatureExtractor(key).n_features == len(expected)


def test_feature_sets_are_nested_a_inside_b_inside_c():
    assert len(FEATURE_SETS["A"]) == 2
    assert len(FEATURE_SETS["B"]) == 5
    assert len(FEATURE_SETS["C"]) == 9
    assert FEATURE_SETS["B"][: len(FEATURE_SETS["A"])] == FEATURE_SETS["A"]
    assert FEATURE_SETS["C"][: len(FEATURE_SETS["B"])] == FEATURE_SETS["B"]
    assert set(FEATURE_SETS) == {"A", "B", "C"}


def test_only_set_c_is_centroid_dependent():
    # A and B are the leakage-free controls of Experiment 2; if either started
    # consuming anchors the ablation would compare nothing.
    assert CENTROID_FEATURE_SETS == frozenset({"C"})
    assert not CalibrationFeatureExtractor("A").requires_centroids
    assert not CalibrationFeatureExtractor("B").requires_centroids
    assert CalibrationFeatureExtractor("C").requires_centroids


@pytest.mark.parametrize("raw, expected", [("a", "A"), (" c ", "C"), ("B\n", "B"), ("C", "C")])
def test_normalise_feature_set_canonicalises_case_and_whitespace(raw, expected):
    assert normalise_feature_set(raw) == expected
    assert CalibrationFeatureExtractor(raw).feature_set == expected


@pytest.mark.parametrize("bad", ["D", "", "AB", "gaze", None, 3])
def test_normalise_feature_set_rejects_anything_else(bad):
    with pytest.raises(ValueError) as excinfo:
        normalise_feature_set(bad)
    assert "['A', 'B', 'C']" in str(excinfo.value)


# ==========================================================================
# features.py -- the fit/transform contract
# ==========================================================================


@pytest.mark.parametrize("key", ["A", "B", "C"])
def test_transform_before_fit_raises_for_every_feature_set(key):
    # A and B need no anchors, but "has this user been calibrated?" must have
    # one answer for all three sets.
    extractor = CalibrationFeatureExtractor(key)
    assert not extractor.is_fitted
    with pytest.raises(RuntimeError, match="before fit"):
        extractor.transform(GazeVector(0.0, 0.0, 1.0), HeadPose())


@pytest.mark.parametrize("key", ["A", "B"])
def test_stateless_sets_fit_on_anything_including_no_samples(key):
    extractor = CalibrationFeatureExtractor(key).fit([])
    assert extractor.is_fitted
    assert extractor.camera_centroid is None and extractor.bottom_centroid is None
    assert extractor.transform(GazeVector(0.1, -0.2, 1.0), HeadPose(1.0, 2.0, 3.0)).shape == (
        len(FEATURE_SETS[key]),
    )


@pytest.mark.parametrize(
    "samples",
    [
        pytest.param([], id="no-samples"),
        pytest.param([_sample("CAMERA", 0.0, 0.0)], id="camera-only"),
        pytest.param([_sample("BOTTOM", 0.0, -0.3)], id="bottom-only"),
        pytest.param(
            [_sample("IGNORE", 0.0, 0.0), _sample("BOTTOM", 0.0, -0.3)], id="camera-all-ignored"
        ),
        pytest.param(
            [_sample("CAMERA", float("nan"), 0.0), _sample("BOTTOM", 0.0, -0.3)],
            id="camera-not-finite",
        ),
    ],
)
def test_set_c_fit_demands_a_usable_sample_of_both_classes(samples):
    extractor = CalibrationFeatureExtractor("C")
    with pytest.raises(ValueError, match="at least one usable CAMERA and one usable BOTTOM"):
        extractor.fit(samples)
    assert not extractor.is_fitted


def test_anchors_ignore_guard_band_and_non_finite_gaze_rows():
    # One NaN gaze row left in by a failed backbone frame would poison the
    # frozen anchor for the whole session.
    samples = [
        _sample("CAMERA", 0.10, 0.20, frame_id=0),
        _sample("CAMERA", 0.20, 0.40, frame_id=1),
        _sample("IGNORE", 9.00, 9.00, frame_id=2),
        _sample("CAMERA", float("nan"), 0.30, frame_id=3),
        _sample("CAMERA", 0.30, float("inf"), frame_id=4),
        _sample("BOTTOM", -0.10, -0.30, frame_id=5),
        _sample("BOTTOM", -0.30, -0.50, frame_id=6),
    ]
    extractor = CalibrationFeatureExtractor("C").fit(samples)

    assert extractor.camera_centroid == pytest.approx([0.15, 0.30])
    assert extractor.bottom_centroid == pytest.approx([-0.20, -0.40])


def test_set_c_delta_columns_are_the_gaze_columns_minus_the_matching_anchor():
    extractor = CalibrationFeatureExtractor("C").fit(
        [_sample("CAMERA", 0.10, 0.20), _sample("BOTTOM", -0.20, -0.40)]
    )
    row = extractor.transform(GazeVector(0.5, -0.25, 0.8), HeadPose(0.01, 0.02, 0.03))

    names = extractor.feature_names()
    gaze_pair = np.asarray([row[names.index("gaze_yaw")], row[names.index("gaze_pitch")]])
    camera_delta = np.asarray(
        [row[names.index("gaze_yaw_minus_camera")], row[names.index("gaze_pitch_minus_camera")]]
    )
    bottom_delta = np.asarray(
        [row[names.index("gaze_yaw_minus_bottom")], row[names.index("gaze_pitch_minus_bottom")]]
    )

    assert camera_delta == pytest.approx(gaze_pair - extractor.camera_centroid, rel=1e-6, abs=1e-7)
    assert bottom_delta == pytest.approx(gaze_pair - extractor.bottom_centroid, rel=1e-6, abs=1e-7)
    # ...and the head columns are untouched raw angles, in radians.
    assert row[names.index("head_yaw")] == pytest.approx(0.01)
    assert row[names.index("head_roll")] == pytest.approx(0.03)
    assert row.dtype == np.float32


def test_raw_columns_are_identical_across_the_three_sets():
    samples = [_sample("CAMERA", 0.10, 0.20), _sample("BOTTOM", -0.20, -0.40)]
    gaze, head = GazeVector(0.5, -0.25, 0.8), HeadPose(0.01, 0.02, 0.03)
    rows = {
        key: CalibrationFeatureExtractor(key).fit(samples).transform(gaze, head) for key in "ABC"
    }

    assert rows["B"][:2] == pytest.approx(rows["A"])
    assert rows["C"][:5] == pytest.approx(rows["B"])


def test_exposed_centroids_are_copies_so_a_caller_cannot_rewrite_the_features():
    extractor = CalibrationFeatureExtractor("C").fit(
        [_sample("CAMERA", 0.10, 0.20), _sample("BOTTOM", -0.20, -0.40)]
    )
    gaze, head = GazeVector(0.5, -0.25, 0.8), HeadPose()
    before = extractor.transform(gaze, head).copy()

    stolen = extractor.camera_centroid
    stolen[:] = 99.0
    extractor.bottom_centroid[:] = -99.0

    assert extractor.camera_centroid == pytest.approx([0.10, 0.20])
    assert extractor.transform(gaze, head) == pytest.approx(before)


def test_only_an_explicit_refit_can_move_the_frozen_anchors(calib_cfg):
    samples = _separable_calibration()
    extractor = CalibrationFeatureExtractor("C").fit(samples)
    gaze, head = GazeVector(0.5, -0.25, 0.8), HeadPose()
    frozen = extractor.transform(gaze, head).copy()

    # Scoring more frames, and re-running the doc 5-2 check with the fitted
    # extractor, must not re-derive the anchors: that is the measured failure
    # mode in the features docstring.
    for _ in range(3):
        extractor.transform(GazeVector(1.0, 1.0, 0.5), HeadPose(0.3, 0.3, 0.3))
    assess_calibration(_pitch_overlap_calibration(gap=0.30), calib_cfg, extractor)
    assert extractor.transform(gaze, head) == pytest.approx(frozen)

    # A calibration retry is a new calibration, and is the one thing that may
    # move them; the raw columns stay put, only the deltas shift.
    extractor.fit([_sample("CAMERA", 1.0, 1.0), _sample("BOTTOM", -1.0, -1.0)])
    after = extractor.transform(gaze, head)
    assert after[:5] == pytest.approx(frozen[:5])
    assert after[5:] != pytest.approx(frozen[5:])
    assert extractor.camera_centroid == pytest.approx([1.0, 1.0])


@pytest.mark.parametrize("key", ["A", "B", "C"])
def test_transform_samples_keeps_row_order_and_shapes_an_empty_batch(key):
    samples = _separable_calibration(n=3)
    extractor = CalibrationFeatureExtractor(key).fit(samples)

    assert extractor.transform_samples([]).shape == (0, len(FEATURE_SETS[key]))
    matrix = extractor.transform_samples(samples)
    assert matrix.shape == (len(samples), len(FEATURE_SETS[key]))
    for i, sample in enumerate(samples):
        assert matrix[i] == pytest.approx(extractor.transform(sample.gaze, sample.head_pose))


# ==========================================================================
# features.py -- persistence
# ==========================================================================


def test_to_dict_round_trip_restores_the_anchors_verbatim():
    original = CalibrationFeatureExtractor("C").fit(_separable_calibration())
    gaze, head = GazeVector(0.5, -0.25, 0.8), HeadPose(0.01, 0.02, 0.03)

    state = original.to_dict()
    restored = CalibrationFeatureExtractor.from_dict(state)

    assert state["feature_set"] == "C"
    assert state["fitted"] is True
    assert state["feature_names"] == list(FEATURE_SETS["C"])
    assert restored.camera_centroid == pytest.approx(original.camera_centroid)
    assert restored.bottom_centroid == pytest.approx(original.bottom_centroid)
    assert restored.transform(gaze, head) == pytest.approx(original.transform(gaze, head))


def test_from_dict_refuses_a_fitted_set_c_state_with_no_anchors():
    with pytest.raises(ValueError, match="saved as fitted but carries no centroids"):
        CalibrationFeatureExtractor.from_dict(
            {
                "feature_set": "C",
                "fitted": True,
                "camera_centroid": [0.0, 0.0],
                "bottom_centroid": None,
            }
        )


@pytest.mark.parametrize(
    "state, fitted_flag",
    [
        ({"feature_set": "A", "fitted": True}, True),
        ({"feature_set": "B", "fitted": True}, True),
        ({"feature_set": "C", "fitted": False}, False),
        ({"feature_set": "B"}, True),  # legacy payload: no flag means fitted
    ],
)
def test_from_dict_accepts_states_that_need_no_anchors(state, fitted_flag):
    extractor = CalibrationFeatureExtractor.from_dict(state)
    assert extractor.is_fitted is fitted_flag
    assert extractor.camera_centroid is None


# ==========================================================================
# quality.py -- what counts as a usable sample
# ==========================================================================


def test_split_by_label_drops_guard_band_unknown_and_non_finite_rows():
    samples = [
        _sample("CAMERA", 0.02, 0.01, frame_id=0),
        _sample("IGNORE", 0.02, 0.01, frame_id=1),
        _sample("BOTTOM", -0.01, -0.33, frame_id=2),
        _sample("CAMERA", float("nan"), 0.01, frame_id=3),
        _sample("BOTTOM", -0.01, float("inf"), frame_id=4),
        _sample("camera", 0.03, 0.02, frame_id=5),  # coerced, not dropped
        _sample("SOMEWHERE_ELSE", 0.0, 0.0, frame_id=6),
        _sample("CAMERA", 0.02, 0.01, head=(0.0, 0.0, float("nan")), frame_id=7),
        _sample("BOTTOM", -0.02, -0.30, frame_id=8),
    ]

    camera, bottom = split_by_label(samples)

    # Order preserved, and the head angles are checked too: row 7 is dropped for
    # a NaN roll even though its gaze pair is finite.
    assert [s.frame_id for s in camera] == [0, 5]
    assert [s.frame_id for s in bottom] == [2, 8]
    # The returned objects are the caller's own, and nothing is mutated.
    assert camera[0] is samples[0]
    assert len(samples) == 9


def test_split_by_label_returns_empty_lists_for_an_empty_calibration():
    assert split_by_label([]) == ([], [])


# ==========================================================================
# quality.py -- the doc 5-2 pass rule
# ==========================================================================


@pytest.mark.parametrize(
    "samples, expected",
    [
        pytest.param(
            [
                _sample("CAMERA", 0.02, 0.02, frame_id=0),
                _sample("BOTTOM", -0.01, -0.33, frame_id=1),
            ],
            CalibrationFailReason.NOT_ENOUGH_SAMPLES,
            id="one-per-class",
        ),
        pytest.param(
            _separable_calibration(n=3),
            CalibrationFailReason.NOT_ENOUGH_SAMPLES,
            id="below-min-samples-per-class",
        ),
        pytest.param(
            _frozen_gaze_calibration(),
            CalibrationFailReason.DEGENERATE_FEATURES,
            id="dead-gaze-signal",
        ),
        pytest.param(
            _pitch_overlap_calibration(gap=0.02),
            CalibrationFailReason.CLASS_NOT_SEPARABLE,
            id="classes-overlap",
        ),
        pytest.param(
            _orthogonal_scatter_calibration(),
            CalibrationFailReason.CENTROIDS_TOO_CLOSE,
            id="centroids-too-close",
        ),
        pytest.param(
            _pitch_overlap_calibration(gap=0.20),
            CalibrationFailReason.LOW_LOO_ACCURACY,
            id="unstable-classifier",
        ),
        pytest.param(_separable_calibration(), None, id="clean-calibration"),
    ],
)
def test_every_retry_reason_stays_reachable_with_the_shipped_thresholds(
    samples, expected, calib_cfg
):
    quality = assess_calibration(
        samples, calib_cfg, CalibrationFeatureExtractor(calib_cfg.feature_set)
    )

    assert quality.reason == (None if expected is None else expected.value)
    assert quality.status == (
        CalibrationStatus.OK.value if expected is None else CalibrationStatus.RETRY_REQUIRED.value
    )
    assert quality.ok is (expected is None)
    # INVERTED_PITCH is advisory and must never turn up here (schemas.py).
    assert quality.reason != CalibrationFailReason.INVERTED_PITCH.value


def test_a_clean_calibration_reports_no_reason_and_no_hint(calib_cfg):
    quality = assess_calibration(
        _separable_calibration(), calib_cfg, CalibrationFeatureExtractor("C")
    )

    assert quality.ok and quality.reason is None and quality.hint is None
    assert quality.n_camera == 12 and quality.n_bottom == 12
    assert quality.separability >= calib_cfg.min_separability
    assert quality.centroid_distance >= calib_cfg.min_centroid_distance
    assert quality.loo_accuracy >= calib_cfg.min_loo_accuracy
    assert quality.to_dict()["calibration_status"] == CalibrationStatus.OK.value


def test_the_count_gate_returns_before_any_statistic_is_computed(calib_cfg):
    # Fewer than two per class: the variance and LOO arithmetic is meaningless,
    # so the report must stay at zero rather than carry fabricated numbers.
    quality = assess_calibration(
        [_sample("CAMERA", 0.02, 0.02, frame_id=0), _sample("BOTTOM", -0.01, -0.33, frame_id=1)],
        calib_cfg,
        CalibrationFeatureExtractor("C"),
    )

    assert quality.reason == CalibrationFailReason.NOT_ENOUGH_SAMPLES.value
    assert quality.n_camera == 1 and quality.n_bottom == 1
    assert quality.camera_centroid == [] and quality.bottom_centroid == []
    assert quality.camera_variance == 0.0 and quality.bottom_variance == 0.0
    assert quality.centroid_distance == 0.0
    assert quality.separability == 0.0
    assert quality.loo_accuracy == 0.0
    assert INVERTED_PITCH_HINT_PREFIX not in (quality.hint or "")


def test_the_configured_count_gate_keeps_the_statistics_it_measured(calib_cfg):
    # Second, distinct NOT_ENOUGH_SAMPLES path: enough samples to measure, too
    # few to trust, so the metrics are real and only the verdict fails.
    quality = assess_calibration(
        _separable_calibration(n=3), calib_cfg, CalibrationFeatureExtractor("C")
    )

    assert quality.reason == CalibrationFailReason.NOT_ENOUGH_SAMPLES.value
    assert quality.n_camera == 3 and quality.n_bottom == 3
    assert len(quality.camera_centroid) == len(FEATURE_SETS["C"])
    assert quality.centroid_distance > 0.0
    assert quality.separability > 0.0
    assert quality.loo_accuracy > 0.0


def test_a_dead_gaze_signal_outranks_a_geometry_failure(calib_cfg):
    # Both gaze columns frozen AND the two classes indistinguishable: the more
    # specific root cause (a dead backbone) has to be the reported one.
    quality = assess_calibration(
        _frozen_gaze_calibration(head_gap=False), calib_cfg, CalibrationFeatureExtractor("C")
    )

    assert quality.separability < calib_cfg.min_separability
    assert quality.centroid_distance < calib_cfg.min_centroid_distance
    assert quality.reason == CalibrationFailReason.DEGENERATE_FEATURES.value
    assert "gaze values never changed" in quality.hint


def test_separability_outranks_centroid_distance(calib_cfg):
    quality = assess_calibration(
        _pitch_overlap_calibration(gap=0.02), calib_cfg, CalibrationFeatureExtractor("C")
    )

    assert quality.separability < calib_cfg.min_separability
    assert quality.centroid_distance < calib_cfg.min_centroid_distance
    assert quality.reason == CalibrationFailReason.CLASS_NOT_SEPARABLE.value


def test_centroid_distance_fails_on_its_own_when_the_fisher_ratio_passes(calib_cfg):
    quality = assess_calibration(
        _orthogonal_scatter_calibration(), calib_cfg, CalibrationFeatureExtractor("C")
    )

    assert quality.separability >= calib_cfg.min_separability
    assert quality.centroid_distance < calib_cfg.min_centroid_distance
    assert quality.reason == CalibrationFailReason.CENTROIDS_TOO_CLOSE.value


def test_low_loo_accuracy_is_the_last_resort_reason(calib_cfg):
    quality = assess_calibration(
        _pitch_overlap_calibration(gap=0.20), calib_cfg, CalibrationFeatureExtractor("C")
    )

    assert quality.separability >= calib_cfg.min_separability
    assert quality.centroid_distance >= calib_cfg.min_centroid_distance
    assert quality.loo_accuracy < calib_cfg.min_loo_accuracy
    assert quality.reason == CalibrationFailReason.LOW_LOO_ACCURACY.value


def test_centroids_are_reported_in_raw_units_while_the_distance_is_standardised(calib_cfg):
    samples = _separable_calibration()
    quality = assess_calibration(samples, calib_cfg, CalibrationFeatureExtractor("C"))
    pitch = FEATURE_SETS["C"].index("gaze_pitch")

    camera_pitch = float(np.mean([s.gaze.gaze_pitch for s in samples if s.label == "CAMERA"]))
    bottom_pitch = float(np.mean([s.gaze.gaze_pitch for s in samples if s.label == "BOTTOM"]))
    assert quality.camera_centroid[pitch] == pytest.approx(camera_pitch, abs=1e-6)
    assert quality.bottom_centroid[pitch] == pytest.approx(bottom_pitch, abs=1e-6)
    # BOTTOM below CAMERA, per the schemas.py sign convention.
    assert quality.bottom_centroid[pitch] < quality.camera_centroid[pitch]

    raw_gap = float(
        np.linalg.norm(np.asarray(quality.camera_centroid) - np.asarray(quality.bottom_centroid))
    )
    # Deliberately not the same number: the reported centroids are radians, the
    # distance is in z-scored feature units.
    assert quality.centroid_distance != pytest.approx(raw_gap, rel=1e-3)


# ==========================================================================
# quality.py -- the advisory pitch-ordering check
# ==========================================================================


def _inverted(samples):
    """The same calibration with the two cues followed the wrong way round."""
    flipped = []
    for sample in samples:
        label = "BOTTOM" if sample.label == "CAMERA" else "CAMERA"
        flipped.append(
            _sample(label, sample.gaze.gaze_yaw, sample.gaze.gaze_pitch, frame_id=sample.frame_id)
        )
    return flipped


@pytest.mark.parametrize(
    "samples",
    [
        pytest.param(_inverted(_separable_calibration()), id="bottom-above-camera"),
        pytest.param(
            [_sample("CAMERA", 0.20 + 0.01 * _rot(k, 0), 0.0, frame_id=k) for k in range(12)]
            + [
                _sample("BOTTOM", -0.20 + 0.01 * _rot(k, 0), 0.0, frame_id=100 + k)
                for k in range(12)
            ],
            id="equal-pitch",
        ),
    ],
)
def test_inverted_pitch_only_warns_and_never_fails_the_calibration(samples, calib_cfg):
    quality = assess_calibration(samples, calib_cfg, CalibrationFeatureExtractor("C"))

    assert quality.status == CalibrationStatus.OK.value
    assert quality.ok
    assert quality.reason is None
    assert quality.hint.startswith(INVERTED_PITCH_HINT_PREFIX)


def test_the_pitch_warning_rides_along_with_a_real_failure_without_replacing_it(calib_cfg):
    quality = assess_calibration(
        _inverted(_separable_calibration(n=3)), calib_cfg, CalibrationFeatureExtractor("C")
    )

    assert quality.reason == CalibrationFailReason.NOT_ENOUGH_SAMPLES.value
    assert quality.reason != CalibrationFailReason.INVERTED_PITCH.value
    assert "Not enough usable frames" in quality.hint
    assert INVERTED_PITCH_HINT_PREFIX in quality.hint


def test_disabling_the_pitch_check_silences_the_advisory(calib_cfg):
    quality = assess_calibration(
        _inverted(_separable_calibration()),
        replace(calib_cfg, check_pitch_ordering=False),
        CalibrationFeatureExtractor("C"),
    )

    assert quality.ok and quality.hint is None


# ==========================================================================
# quality.py -- leave-one-out and the estimator factory
# ==========================================================================


@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_leave_one_out_returns_zero_instead_of_raising_on_a_tiny_set(n, calib_cfg):
    samples = _separable_calibration(n=2)[:n]
    labels = [s.label for s in samples]

    assert leave_one_out_accuracy(samples, labels, calib_cfg, "A") == 0.0


def test_leave_one_out_returns_zero_when_a_fold_would_drop_a_whole_class(calib_cfg):
    samples = _separable_calibration(n=3)
    samples = samples[:1] + samples[3:]  # 1 CAMERA, 3 BOTTOM -> a single-class fold
    labels = [s.label for s in samples]

    assert leave_one_out_accuracy(samples, labels, calib_cfg, "C") == 0.0


def test_leave_one_out_scores_a_calibration_without_touching_the_caller(calib_cfg):
    samples = _separable_calibration(n=6)
    labels = [s.label for s in samples]
    frozen = [(s.label, s.gaze.gaze_pitch, s.frame_id) for s in samples]

    score = leave_one_out_accuracy(samples, labels, calib_cfg, "C")

    assert 0.0 <= score <= 1.0
    assert [(s.label, s.gaze.gaze_pitch, s.frame_id) for s in samples] == frozen


def test_the_assessed_and_the_shipped_estimator_are_built_by_one_factory(calib_cfg):
    pipeline = build_calibration_pipeline(calib_cfg)

    assert [name for name, _ in pipeline.steps] == ["scaler", "lr"]
    assert pipeline.named_steps["lr"].C == pytest.approx(float(calib_cfg.C))
    assert pipeline.named_steps["lr"].max_iter == int(calib_cfg.max_iter)
    assert pipeline.named_steps["lr"].random_state == int(calib_cfg.random_seed)
    assert pipeline.named_steps["lr"].class_weight == calib_cfg.class_weight


@pytest.mark.parametrize("value", ["none", "None", "", "  NULL  ", None])
def test_a_stringy_none_class_weight_becomes_a_real_none(value, calib_cfg):
    pipeline = build_calibration_pipeline(replace(calib_cfg, class_weight=value))
    assert pipeline.named_steps["lr"].class_weight is None


def test_assess_fits_an_unfitted_extractor_on_usable_samples_only(calib_cfg):
    samples = _separable_calibration()
    samples.insert(0, _sample("IGNORE", 5.0, 5.0, frame_id=999))
    extractor = CalibrationFeatureExtractor("C")

    assess_calibration(samples, calib_cfg, extractor)

    assert extractor.is_fitted
    usable = [s.gaze.gaze_pitch for s in samples if s.label == "CAMERA"]
    assert extractor.camera_centroid[1] == pytest.approx(float(np.mean(usable)))


def test_assess_never_moves_anchors_that_are_already_frozen(calib_cfg):
    extractor = CalibrationFeatureExtractor("C").fit(_separable_calibration())
    frozen_camera = extractor.camera_centroid
    frozen_bottom = extractor.bottom_centroid

    assess_calibration(_pitch_overlap_calibration(gap=0.30), calib_cfg, extractor)

    assert extractor.camera_centroid == pytest.approx(frozen_camera)
    assert extractor.bottom_centroid == pytest.approx(frozen_bottom)


# ==========================================================================
# classifier.py -- the sklearn class-order trap
# ==========================================================================


def test_sklearn_sorts_bottom_into_predict_proba_column_zero(fitted):
    # Documented trap: DECISION_CLASSES is (CAMERA, BOTTOM) but the fitted
    # estimator sorts its labels, so the columns come back the other way round.
    pipeline = fitted._pipeline
    assert [str(c) for c in pipeline.classes_] == ["BOTTOM", "CAMERA"]
    assert DECISION_CLASSES == ("CAMERA", "BOTTOM")
    assert list(pipeline.classes_) != list(DECISION_CLASSES)

    gaze, head = GazeVector(-0.01, -0.33, 0.9), HeadPose()
    raw = pipeline.predict_proba(fitted.extractor.transform(gaze, head).reshape(1, -1))[0]
    p_camera, p_bottom = fitted.predict_proba(gaze, head)

    assert p_bottom == pytest.approx(raw[0])
    assert p_camera == pytest.approx(raw[1])


@pytest.mark.parametrize(
    "classes, expected",
    [
        (["BOTTOM", "CAMERA"], (1, 0)),
        (["CAMERA", "BOTTOM"], (0, 1)),
        (np.asarray(["BOTTOM", "CAMERA"]), (1, 0)),
    ],
)
def test_class_indices_are_read_off_the_estimator_not_assumed(classes, expected):
    assert _class_indices(SimpleNamespace(classes_=classes)) == expected


def test_class_indices_fall_back_to_a_fixed_order_without_an_estimator():
    assert _class_indices(None) == (0, 1)


def test_the_two_class_probabilities_sum_to_one(fitted):
    p_camera, p_bottom = fitted.predict_proba(GazeVector(0.02, 0.02, 0.9), HeadPose())
    assert p_camera + p_bottom == pytest.approx(1.0)


def test_a_calibrated_model_puts_each_anchor_on_its_own_side(fitted):
    # Not a score: the two hand-built clusters must simply land on the side of
    # the boundary they were built on, or every threshold test below is moot.
    p_camera, _ = fitted.predict_proba(GazeVector(0.02, 0.02, 0.9), HeadPose())
    _, p_bottom = fitted.predict_proba(GazeVector(-0.01, -0.33, 0.9), HeadPose(0.0, -0.05, 0.0))

    assert p_camera > 0.5
    assert p_bottom > 0.5


# ==========================================================================
# classifier.py -- fitting
# ==========================================================================


def test_predict_proba_on_an_unfitted_classifier_raises(calib_cfg):
    classifier, quality = PerUserGazeClassifier.fit(
        [s for s in _separable_calibration() if s.label == "CAMERA"], calib_cfg
    )

    assert not classifier.is_fitted
    assert quality.reason == CalibrationFailReason.NOT_ENOUGH_SAMPLES.value
    assert quality.n_camera == 12 and quality.n_bottom == 0
    with pytest.raises(RuntimeError, match="not fitted"):
        classifier.predict_proba(GazeVector(0.02, 0.02, 0.9), HeadPose())
    with pytest.raises(RuntimeError, match="refusing to save"):
        classifier.save("never-written.joblib")


def test_fit_trains_a_model_even_when_the_quality_check_demands_a_retry(calib_cfg):
    # doc 5-3: the retry policy belongs to the caller, so a trainable but poor
    # calibration still yields a usable estimator with a failing report.
    classifier, quality = PerUserGazeClassifier.fit(
        _pitch_overlap_calibration(gap=0.02), calib_cfg
    )

    assert classifier.is_fitted
    assert not quality.ok
    assert quality.reason == CalibrationFailReason.CLASS_NOT_SEPARABLE.value
    assert classifier.quality is quality
    p_camera, p_bottom = classifier.predict_proba(GazeVector(0.0, 0.0, 0.9), HeadPose())
    assert p_camera + p_bottom == pytest.approx(1.0)


def test_fit_freezes_the_anchors_the_shipped_model_actually_uses(calib_cfg):
    samples = _separable_calibration()
    samples.append(_sample("IGNORE", 5.0, 5.0, frame_id=999))
    classifier, quality = PerUserGazeClassifier.fit(samples, calib_cfg)

    camera_gaze = np.asarray(
        [[s.gaze.gaze_yaw, s.gaze.gaze_pitch] for s in samples if s.label == "CAMERA"]
    )
    assert classifier.extractor.camera_centroid == pytest.approx(camera_gaze.mean(axis=0))
    assert classifier.feature_names() == list(FEATURE_SETS[calib_cfg.feature_set])
    assert quality.camera_centroid[:2] == pytest.approx(camera_gaze.mean(axis=0), abs=1e-6)


# ==========================================================================
# classifier.py -- the doc 5-4 decision rule
# ==========================================================================


@pytest.mark.parametrize(
    "face_valid, invalid_reason, gaze, expected",
    [
        (False, InvalidReason.EYES_CLOSED.value, None, InvalidReason.EYES_CLOSED.value),
        (False, InvalidReason.FACE_TOO_SMALL.value, None, InvalidReason.FACE_TOO_SMALL.value),
        (False, None, None, InvalidReason.NO_FACE.value),
        (True, None, None, InvalidReason.BACKBONE_FAILED.value),
        (True, None, GazeVector(float("nan"), 0.0, 0.5), InvalidReason.BACKBONE_FAILED.value),
        (True, None, GazeVector(0.0, float("inf"), 0.5), InvalidReason.BACKBONE_FAILED.value),
        (
            True,
            InvalidReason.CROP_FAILED.value,
            GazeVector(float("nan"), 0.0, 0.5),
            InvalidReason.CROP_FAILED.value,
        ),
    ],
)
def test_an_unusable_frame_carries_the_preprocess_reason_forward(
    face_valid, invalid_reason, gaze, expected, fitted
):
    # doc 19 buckets need NO_FACE distinguishable from EYES_CLOSED, and a valid
    # face whose backbone died must not be reported as face_valid: that would
    # let a dead backbone hold the smoother in its last state forever.
    decision = fitted.decide(
        _observation(face_valid=face_valid, invalid_reason=invalid_reason), gaze, latency_ms=9.5
    )

    assert decision.label == GazeState.UNCERTAIN.value
    assert decision.uncertain_reason == expected
    assert decision.face_valid is False
    assert decision.p_camera == 0.5 and decision.p_bottom == 0.5
    assert decision.gaze is gaze
    assert decision.latency_ms == 9.5


def test_an_unusable_frame_is_decided_without_consulting_the_model(calib_cfg):
    # The invalid branch runs before predict_proba, so a session whose
    # calibration failed can still emit UNCERTAIN frames instead of crashing.
    classifier = PerUserGazeClassifier(CalibrationFeatureExtractor("C"), None, calib_cfg)

    decision = classifier.decide(_observation(face_valid=False), None)

    assert decision.label == GazeState.UNCERTAIN.value
    assert decision.uncertain_reason == InvalidReason.NO_FACE.value


@pytest.mark.parametrize(
    "p_camera, expected_label, expected_reason",
    [
        (0.50, GazeState.UNCERTAIN.value, UNCERTAIN_LOW_CONFIDENCE),
        (0.69, GazeState.UNCERTAIN.value, UNCERTAIN_LOW_CONFIDENCE),
        (0.70, GazeState.CAMERA.value, None),  # the floor is inclusive
        (0.71, GazeState.CAMERA.value, None),
        (0.31, GazeState.UNCERTAIN.value, UNCERTAIN_LOW_CONFIDENCE),
        (0.30, GazeState.BOTTOM.value, None),
        (0.05, GazeState.BOTTOM.value, None),
    ],
)
def test_the_confidence_floor_is_the_binding_gate(
    p_camera, expected_label, expected_reason, calib_cfg
):
    classifier = _rule_only(calib_cfg, p_camera, 1.0 - p_camera)

    decision = classifier.decide(_observation(), GazeVector(0.0, -0.1, 0.9))

    assert decision.label == expected_label
    assert decision.uncertain_reason == expected_reason
    assert decision.face_valid is True


@pytest.mark.parametrize("p_camera", [0.0, 0.05, 0.3, 0.5, 0.7, 0.85, 1.0])
def test_low_margin_is_unreachable_at_the_shipped_thresholds(p_camera, calib_cfg):
    # For two classes margin == 2 * p_max - 1, so p_max >= 0.70 already implies
    # margin >= 0.40, twice the shipped 0.20 floor.  Both branches stay in the
    # code because doc 19 sweeps them independently -- see the test below.
    assert calib_cfg.margin_threshold < 2.0 * calib_cfg.p_max_threshold - 1.0

    decision = _rule_only(calib_cfg, p_camera, 1.0 - p_camera).decide(
        _observation(), GazeVector(0.0, -0.1, 0.9)
    )

    assert decision.margin == pytest.approx(2.0 * decision.p_max - 1.0)
    assert decision.uncertain_reason != UNCERTAIN_LOW_MARGIN


def test_low_margin_becomes_reachable_once_the_margin_floor_is_raised(calib_cfg):
    cfg = replace(calib_cfg, margin_threshold=0.45)
    assert cfg.margin_threshold > 2.0 * cfg.p_max_threshold - 1.0

    decision = _rule_only(cfg, 0.72, 0.28).decide(_observation(), GazeVector(0.0, -0.1, 0.9))

    assert decision.label == GazeState.UNCERTAIN.value
    assert decision.uncertain_reason == UNCERTAIN_LOW_MARGIN
    # ...and the confidence floor still gets first refusal.
    lower = _rule_only(replace(cfg, p_max_threshold=0.80), 0.72, 0.28).decide(
        _observation(), GazeVector(0.0, -0.1, 0.9)
    )
    assert lower.uncertain_reason == UNCERTAIN_LOW_CONFIDENCE


def test_a_dead_heat_resolves_to_camera(calib_cfg):
    # Only reachable once both floors are lowered, but the tie-break itself is
    # contract: p_camera >= p_bottom means CAMERA, never BOTTOM, never a crash.
    cfg = replace(calib_cfg, p_max_threshold=0.40, margin_threshold=0.0)

    decision = _rule_only(cfg, 0.5, 0.5).decide(_observation(), GazeVector(0.0, -0.1, 0.9))

    assert decision.label == GazeState.CAMERA.value
    assert decision.uncertain_reason is None
    assert decision.margin == 0.0


def test_a_decided_frame_keeps_its_frame_identity_and_gaze(fitted):
    obs = _observation(frame_id=42, t_ms=5250)
    gaze = GazeVector(-0.01, -0.33, 0.88)

    decision = fitted.decide(obs, gaze, latency_ms=31.25)

    assert decision.frame_id == 42 and decision.t_ms == 5250
    assert decision.face_valid is True
    assert decision.gaze is gaze
    assert decision.latency_ms == 31.25
    assert decision.label in {GazeState.CAMERA.value, GazeState.BOTTOM.value}
    assert decision.to_dict()["label"] == decision.label


# ==========================================================================
# classifier.py -- persistence
# ==========================================================================


def test_save_load_round_trip_preserves_anchors_config_quality_and_scores(fitted, tmp_path):
    target = fitted.save(tmp_path / "nested" / "model.joblib")
    restored = PerUserGazeClassifier.load(target)

    assert target.is_file()
    gaze, head = GazeVector(0.011, -0.14, 0.77), HeadPose(0.01, -0.02, 0.005)
    assert restored.predict_proba(gaze, head) == pytest.approx(fitted.predict_proba(gaze, head))
    assert restored.extractor.camera_centroid == pytest.approx(fitted.extractor.camera_centroid)
    assert restored.extractor.bottom_centroid == pytest.approx(fitted.extractor.bottom_centroid)
    assert restored.feature_names() == fitted.feature_names()
    assert restored.config == fitted.config
    assert restored.quality.status == fitted.quality.status
    assert restored.quality.loo_accuracy == pytest.approx(fitted.quality.loo_accuracy)


def test_a_saved_payload_carries_the_schema_version_and_the_frozen_anchors(fitted, tmp_path):
    payload = joblib.load(fitted.save(tmp_path / "model.joblib"))

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["feature_names"] == fitted.feature_names()
    assert payload["extractor"]["camera_centroid"] == pytest.approx(
        list(fitted.extractor.camera_centroid)
    )
    assert payload["config"]["feature_set"] == fitted.config.feature_set
    assert "sklearn_version" in payload


@pytest.mark.parametrize("version", ["gaze_calib_v0", "", None, "gaze_calib_v1 "])
def test_load_refuses_a_foreign_or_stale_schema_version(fitted, tmp_path, version):
    payload = joblib.load(fitted.save(tmp_path / "model.joblib"))
    payload["schema_version"] = version
    path = tmp_path / "stale.joblib"
    joblib.dump(payload, path)

    with pytest.raises(ValueError, match="schema mismatch"):
        PerUserGazeClassifier.load(path)


def test_load_refuses_feature_names_that_do_not_match_the_feature_set(fitted, tmp_path):
    payload = joblib.load(fitted.save(tmp_path / "model.joblib"))
    payload["feature_names"] = ["gaze_yaw", "gaze_pitch"]
    path = tmp_path / "renamed.joblib"
    joblib.dump(payload, path)

    with pytest.raises(ValueError, match="do not match feature set"):
        PerUserGazeClassifier.load(path)


def test_load_refuses_a_payload_that_is_not_a_model(tmp_path):
    path = tmp_path / "foreign.joblib"
    joblib.dump([1, 2, 3], path)

    with pytest.raises(ValueError, match="not a PerUserGazeClassifier payload"):
        PerUserGazeClassifier.load(path)
