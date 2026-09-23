"""Calibration pre-flight check (doc 5-2).

Four seconds of calibration is all the personalisation this system gets, so a
bad calibration is worth catching *before* it silently poisons a whole take.
This module answers one question -- "are this user's CAMERA and BOTTOM frames
actually distinguishable?" -- and, when they are not, names the most specific
reason plus a hint the UI can show on the retry screen.

Scale convention.  ``centroid_distance``, ``camera_variance`` and
``bottom_variance`` are measured in *standardised* feature units: the design
matrix is z-scored over the calibration samples first, exactly as the doc 5-3
``StandardScaler`` does, so a single threshold means the same thing for a
gaze-only feature set and for the 9-dim set C.  ``camera_centroid`` and
``bottom_centroid``, by contrast, are reported in raw feature units (radians,
column order = ``extractor.feature_names()``) because those are the numbers a
human debugs with -- so the norm of their difference is deliberately *not*
``centroid_distance``.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from vision.config import CalibrationConfig
from vision.schemas import (
    INVERTED_PITCH_HINT_PREFIX,
    CalibrationFailReason,
    CalibrationQuality,
    CalibrationSample,
    CalibrationStatus,
    GazeLabel,
)

from vision.calibration.features import CalibrationFeatureExtractor

#: A column whose standard deviation is below this never moved during the whole
#: calibration; z-scoring it would amplify float noise into a fake feature.
_MIN_STD = 1e-8

#: Floor on the pooled within-class spread so a perfectly rigid calibration
#: yields a huge-but-finite separability instead of an inf that breaks JSON.
_MIN_POOLED_STD = 1e-6

#: Both the leave-one-out estimate and the shipped model need at least this many
#: samples per class before any of the fold arithmetic is meaningful.
_MIN_SAMPLES_FOR_STATS = 2

_HINTS: Dict[CalibrationFailReason, str] = {
    CalibrationFailReason.NOT_ENOUGH_SAMPLES: (
        "Not enough usable frames. Keep your whole face in the camera and hold each cue "
        "until it disappears, then try again."
    ),
    CalibrationFailReason.DEGENERATE_FEATURES: (
        "The gaze values never changed during calibration. Check the camera and the light, "
        "then try again."
    ),
    CalibrationFailReason.CLASS_NOT_SEPARABLE: (
        "CAMERA and BOTTOM look almost the same. Look straight into the camera for the first "
        "cue, then clearly at the bottom centre of the video for the second one."
    ),
    CalibrationFailReason.CENTROIDS_TOO_CLOSE: (
        "The two gaze positions are too close together. Look more clearly toward the bottom "
        "of the video if you choose to recalibrate."
    ),
    CalibrationFailReason.LOW_LOO_ACCURACY: (
        "Calibration is unstable. Sit still, keep the same distance from the camera, and "
        "try again."
    ),
}

#: doc schemas.py: BOTTOM normally sits lower than CAMERA in gaze pitch. A
#: violation may be a reversed user cue or a backbone sign convention, so it is
#: an advisory warning rather than a hard gate.
#: Advisory warning prefixed onto ``CalibrationQuality.hint``.  It is the ONLY
#: runtime signal for ``CalibrationFailReason.INVERTED_PITCH``, which is never
#: assigned to ``quality.reason`` -- an inverted ordering warns, it does not
#: fail.  The prefix comes from schemas so consumers can match on the constant.
_INVERTED_PITCH_HINT = (
    f"{INVERTED_PITCH_HINT_PREFIX} the BOTTOM gaze pitch is not below CAMERA. Check whether "
    "the two targets were followed in reverse; calibration can still run as an advisory model."
)


def build_calibration_pipeline(cfg: CalibrationConfig) -> Pipeline:
    """The doc 5-3 estimator: ``StandardScaler`` + ``LogisticRegression``.

    It is defined here, next to the leave-one-out estimate, so that the number
    reported by :func:`assess_calibration` and the model actually shipped by
    ``PerUserGazeClassifier`` are the same estimator by construction rather than
    by two copies of the same argument list drifting apart.
    """
    class_weight = cfg.class_weight
    if isinstance(class_weight, str) and class_weight.strip().lower() in {"", "none", "null"}:
        class_weight = None
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "lr",
                LogisticRegression(
                    C=float(cfg.C),
                    max_iter=int(cfg.max_iter),
                    class_weight=class_weight,
                    random_state=int(cfg.random_seed),
                ),
            ),
        ]
    )


def split_by_label(
    samples: Sequence[CalibrationSample],
) -> Tuple[List[CalibrationSample], List[CalibrationSample]]:
    """Usable CAMERA and BOTTOM samples, in input order.

    IGNORE frames (doc 4-2 guard bands) and frames whose angles are not finite
    are dropped here, so ``n_camera``/``n_bottom`` count what the model can
    really train on rather than what the collector attempted.
    """
    camera: List[CalibrationSample] = []
    bottom: List[CalibrationSample] = []
    for sample in samples:
        try:
            label = GazeLabel.coerce(sample.label)
        except ValueError:
            continue
        if label is GazeLabel.IGNORE:
            continue
        angles = np.asarray(
            [
                sample.gaze.gaze_yaw,
                sample.gaze.gaze_pitch,
                sample.head_pose.yaw,
                sample.head_pose.pitch,
                sample.head_pose.roll,
            ],
            dtype=np.float64,
        )
        if not np.all(np.isfinite(angles)):
            continue
        (camera if label is GazeLabel.CAMERA else bottom).append(sample)
    return camera, bottom


def leave_one_out_accuracy(
    samples: Sequence[CalibrationSample],
    labels: Sequence[str],
    cfg: CalibrationConfig,
    feature_set: str,
) -> float:
    """Leave-one-out accuracy of the doc 5-3 pipeline over the calibration set.

    The feature extractor is refitted on each fold's training subset rather than
    reused from the full-set fit, because for set C the centroids are model
    parameters and a held-out frame must not contribute to its own features.
    With the doc 5-3 scaler in front the two variants happen to agree exactly --
    the anchor shift a dropped frame causes is a per-column constant, which
    ``StandardScaler`` centres away (measured: identical on 30/30 synthetic
    calibrations) -- so this is insurance, at the cost of ~32 extra means, for
    the day the pipeline's first step is not a mean-centring scaler.
    """
    n = len(samples)
    y = np.asarray(labels)
    if n < 2 * _MIN_SAMPLES_FOR_STATS:
        return 0.0
    correct = 0
    for i in range(n):
        keep = [j for j in range(n) if j != i]
        train = [samples[j] for j in keep]
        y_train = y[keep]
        if len(np.unique(y_train)) < 2:
            return 0.0
        fold_extractor = CalibrationFeatureExtractor(feature_set).fit(train)
        pipeline = build_calibration_pipeline(cfg)
        pipeline.fit(fold_extractor.transform_samples(train), y_train)
        held_out = fold_extractor.transform(samples[i].gaze, samples[i].head_pose)
        correct += int(pipeline.predict(held_out.reshape(1, -1))[0] == y[i])
    return float(correct) / float(n)


def _fail(
    reason: CalibrationFailReason,
    quality: CalibrationQuality,
    warning: Optional[str] = None,
) -> CalibrationQuality:
    quality.status = CalibrationStatus.RETRY_REQUIRED.value
    quality.reason = reason.value
    hint = _HINTS[reason]
    quality.hint = f"{hint} {warning}" if warning else hint
    return quality


def assess_calibration(
    samples: Sequence[CalibrationSample],
    cfg: CalibrationConfig,
    extractor: CalibrationFeatureExtractor,
) -> CalibrationQuality:
    """Score a calibration set and apply the doc 5-2 pass rule.

    ``extractor`` is fitted here when it is not fitted already, on these samples
    and only these samples -- the doc 5-1 leakage rule.  Passing an
    already-fitted extractor (what ``PerUserGazeClassifier.fit`` does) reuses
    the anchors it froze rather than moving them.

    The pass rule is the conjunction of all four thresholds; on failure the
    reason is the most specific one that applies, ordered by root cause:
    NOT_ENOUGH_SAMPLES (nothing else is measurable) -> DEGENERATE_FEATURES (the
    gaze signal is dead) -> CLASS_NOT_SEPARABLE (Fisher ratio, the geometric
    cause) -> CENTROIDS_TOO_CLOSE (tight but barely apart) -> LOW_LOO_ACCURACY
    (geometry looked fine, the classifier still failed).
    """
    camera, bottom = split_by_label(samples)
    n_camera, n_bottom = len(camera), len(bottom)
    quality = CalibrationQuality(
        status=CalibrationStatus.OK.value, n_camera=n_camera, n_bottom=n_bottom
    )

    # Below two per class the variance and LOO arithmetic has no meaning, so
    # report the count failure and stop rather than emit fabricated statistics.
    if min(n_camera, n_bottom) < _MIN_SAMPLES_FOR_STATS:
        return _fail(CalibrationFailReason.NOT_ENOUGH_SAMPLES, quality)

    if not extractor.is_fitted:
        extractor.fit(camera + bottom)

    ordered = list(camera) + list(bottom)
    labels = [GazeLabel.CAMERA.value] * n_camera + [GazeLabel.BOTTOM.value] * n_bottom
    features = extractor.transform_samples(ordered).astype(np.float64)
    names = extractor.feature_names()

    raw_camera_centroid = features[:n_camera].mean(axis=0)
    raw_bottom_centroid = features[n_camera:].mean(axis=0)
    quality.camera_centroid = [float(v) for v in raw_camera_centroid]
    quality.bottom_centroid = [float(v) for v in raw_bottom_centroid]

    sigma = features.std(axis=0)
    dead = sigma <= _MIN_STD
    scaled = (features - features.mean(axis=0)) / np.where(dead, 1.0, sigma)

    centroid_camera = scaled[:n_camera].mean(axis=0)
    centroid_bottom = scaled[n_camera:].mean(axis=0)
    delta = centroid_camera - centroid_bottom
    centroid_distance = float(np.linalg.norm(delta))
    # Read the distance against the feature set, never as an absolute:
    # standardisation gives every column unit total variance, so more columns
    # means more distance, and set C's delta columns are the gaze columns
    # shifted by a constant -- identical after centring -- which counts the gaze
    # separation three times.  Same synthetic calibration, three sets: A 1.99,
    # B 2.79, C 3.96.  ``separability`` below is a ratio along one axis and is
    # immune to both effects, which is why it, and not this, is the primary
    # criterion.
    quality.centroid_distance = centroid_distance

    # Total within-class scatter: mean squared distance to the own centroid.
    # A non-discriminative column contributes about 1 to it, so the number is
    # only meaningful next to the other class's and next to the distance above.
    quality.camera_variance = float(
        np.mean(np.sum((scaled[:n_camera] - centroid_camera) ** 2, axis=1))
    )
    quality.bottom_variance = float(
        np.mean(np.sum((scaled[n_camera:] - centroid_bottom) ** 2, axis=1))
    )

    # Fisher-like separability along the between-centroid axis only.  Measuring
    # the spread in all 9 dims instead would let noise in head_roll -- a
    # direction the classifier is free to ignore -- veto a clean calibration.
    if centroid_distance > _MIN_POOLED_STD:
        axis = delta / centroid_distance
        projected_camera = scaled[:n_camera] @ axis
        projected_bottom = scaled[n_camera:] @ axis
        pooled = np.sqrt(
            (
                (n_camera - 1) * np.var(projected_camera, ddof=1)
                + (n_bottom - 1) * np.var(projected_bottom, ddof=1)
            )
            / float(n_camera + n_bottom - 2)
        )
        quality.separability = float(centroid_distance / max(float(pooled), _MIN_POOLED_STD))
    else:
        quality.separability = 0.0

    quality.loo_accuracy = leave_one_out_accuracy(ordered, labels, cfg, extractor.feature_set)

    # doc schemas.py sign convention check; a warning, never a hard failure.
    warning: Optional[str] = None
    if cfg.check_pitch_ordering:
        pitch = names.index("gaze_pitch")
        if not raw_bottom_centroid[pitch] < raw_camera_centroid[pitch]:
            warning = _INVERTED_PITCH_HINT

    if min(n_camera, n_bottom) < int(cfg.min_samples_per_class):
        return _fail(CalibrationFailReason.NOT_ENOUGH_SAMPLES, quality, warning)
    # "The gaze never moved" is a dead backbone or a frozen camera, which is a
    # different fix from "the two classes overlap", so it gets its own reason.
    if bool(dead[names.index("gaze_yaw")]) and bool(dead[names.index("gaze_pitch")]):
        return _fail(CalibrationFailReason.DEGENERATE_FEATURES, quality, warning)
    if quality.separability < float(cfg.min_separability):
        return _fail(CalibrationFailReason.CLASS_NOT_SEPARABLE, quality, warning)
    if quality.centroid_distance < float(cfg.min_centroid_distance):
        return _fail(CalibrationFailReason.CENTROIDS_TOO_CLOSE, quality, warning)
    if quality.loo_accuracy < float(cfg.min_loo_accuracy):
        return _fail(CalibrationFailReason.LOW_LOO_ACCURACY, quality, warning)

    quality.hint = warning
    return quality
