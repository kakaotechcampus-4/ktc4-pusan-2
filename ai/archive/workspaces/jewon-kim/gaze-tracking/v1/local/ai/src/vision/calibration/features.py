"""Per-user calibration features (doc 5-1; Experiment 2 ablation sets, doc 23).

The classifier of doc 5-3 is deliberately tiny -- four seconds of calibration
cannot support anything bigger -- so every bit of modelling power lives in
these features.  Three sets exist because doc 23 Experiment 2 asks what each
group of inputs is actually worth::

    A   gaze yaw/pitch only                              2 dims
    B   A + head yaw/pitch/roll                          5 dims
    C   B + gaze deltas from both class centroids        9 dims   <- doc 5-1 v1

Set C is the default.  The same absolute gaze angle means different things for
different faces and camera placements, so the discriminative quantity is where
the current gaze sits relative to *this user's* own CAMERA and BOTTOM anchors,
measured during their own calibration.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from vision.schemas import CalibrationSample, GazeLabel, GazeVector, HeadPose

#: The five raw angles every set starts from.  The order mirrors
#: ``CalibrationSample.raw_vector`` in schemas.py and must stay in sync with it.
_RAW_NAMES: Tuple[str, ...] = (
    "gaze_yaw",
    "gaze_pitch",
    "head_yaw",
    "head_pitch",
    "head_roll",
)

#: Set C's four extra dims: the gaze angles re-expressed against each anchor.
_DELTA_NAMES: Tuple[str, ...] = (
    "gaze_yaw_minus_camera",
    "gaze_pitch_minus_camera",
    "gaze_yaw_minus_bottom",
    "gaze_pitch_minus_bottom",
)

FEATURE_SETS: Dict[str, Tuple[str, ...]] = {
    "A": _RAW_NAMES[:2],
    "B": _RAW_NAMES,
    "C": _RAW_NAMES + _DELTA_NAMES,
}

#: Only C consumes calibration centroids; A and B are stateless by construction,
#: which is what makes them the leakage-free controls in Experiment 2.
CENTROID_FEATURE_SETS = frozenset({"C"})


def normalise_feature_set(feature_set: str) -> str:
    """Validate and canonicalise a feature-set key coming from config (doc 23)."""
    key = str(feature_set).strip().upper()
    if key not in FEATURE_SETS:
        raise ValueError(
            f"unknown feature_set {feature_set!r}; expected one of {sorted(FEATURE_SETS)}"
        )
    return key


def _raw_angles(gaze: GazeVector, head: HeadPose) -> np.ndarray:
    return np.asarray(
        [gaze.gaze_yaw, gaze.gaze_pitch, head.yaw, head.pitch, head.roll],
        dtype=np.float64,
    )


def _class_gaze(samples: Sequence[CalibrationSample], label: GazeLabel) -> np.ndarray:
    """(N, 2) gaze yaw/pitch for one class, non-finite rows dropped.

    A backbone that failed on a frame can leave NaNs behind; letting one through
    would poison the frozen anchor for the whole session.
    """
    rows = []
    for sample in samples:
        try:
            if GazeLabel.coerce(sample.label) is not label:
                continue
        except ValueError:
            continue
        pair = np.asarray([sample.gaze.gaze_yaw, sample.gaze.gaze_pitch], dtype=np.float64)
        if np.all(np.isfinite(pair)):
            rows.append(pair)
    if not rows:
        return np.zeros((0, 2), dtype=np.float64)
    return np.vstack(rows)


class CalibrationFeatureExtractor:
    """Maps one ``(GazeVector, HeadPose)`` pair to a feature row (doc 5-1).

    Leakage rule (doc 5-1, doc 4-3).  The CAMERA and BOTTOM centroids that set C
    subtracts are estimated once, inside :meth:`fit`, from calibration samples
    only, and are frozen from that moment on.  Every later :meth:`transform` --
    every evaluation frame, every live frame, every frame scored by a model
    restored from disk -- reuses those same frozen numbers.  That is also why
    :meth:`to_dict` persists the anchors verbatim instead of re-deriving them at
    load time.

    What the freeze actually buys, given the doc 5-3 ``StandardScaler``: the
    delta columns are the gaze columns minus a per-session constant, and the
    scaler subtracts each column's training mean, so the anchor *value* cancels
    out as long as fit time and inference time use the same one.  The failure
    mode is therefore not a badly chosen anchor but a *moved* one -- refitting
    the anchors on evaluation frames under an already-trained model shifts every
    delta column away from the mean the scaler stored and rewrites the decision
    boundary in gaze space (measured: 87 of 300 probe frames flip class).  It is
    the constancy, not the number, that has to be defended.

    :meth:`fit` is required even for A and B, which need no anchors, so that
    "has this user been calibrated?" has one answer for every feature set.
    """

    def __init__(self, feature_set: str = "C") -> None:
        self.feature_set = normalise_feature_set(feature_set)
        self._camera_centroid: Optional[np.ndarray] = None
        self._bottom_centroid: Optional[np.ndarray] = None
        self._fitted = False

    # -- introspection ----------------------------------------------------
    @property
    def is_fitted(self) -> bool:
        return self._fitted

    @property
    def requires_centroids(self) -> bool:
        return self.feature_set in CENTROID_FEATURE_SETS

    @property
    def n_features(self) -> int:
        return len(FEATURE_SETS[self.feature_set])

    @property
    def camera_centroid(self) -> Optional[np.ndarray]:
        """Frozen CAMERA gaze anchor ``(yaw, pitch)``.

        Returns a copy: an in-place edit by a caller would silently rewrite the
        feature definition for the rest of the session.
        """
        return None if self._camera_centroid is None else self._camera_centroid.copy()

    @property
    def bottom_centroid(self) -> Optional[np.ndarray]:
        return None if self._bottom_centroid is None else self._bottom_centroid.copy()

    def feature_names(self) -> List[str]:
        """Column names in the exact order :meth:`transform` emits them."""
        return list(FEATURE_SETS[self.feature_set])

    # -- fitting ----------------------------------------------------------
    def fit(self, samples: Sequence[CalibrationSample]) -> "CalibrationFeatureExtractor":
        """Freeze the anchors from calibration samples (doc 5-1).

        Refitting is allowed only because a calibration *retry* (doc 5-2) is a
        new calibration; nothing on the inference path may call this.
        """
        if self.requires_centroids:
            camera = _class_gaze(samples, GazeLabel.CAMERA)
            bottom = _class_gaze(samples, GazeLabel.BOTTOM)
            if len(camera) == 0 or len(bottom) == 0:
                raise ValueError(
                    f"feature set {self.feature_set} needs at least one usable CAMERA and one "
                    f"usable BOTTOM sample; got {len(camera)} and {len(bottom)}"
                )
            self._camera_centroid = camera.mean(axis=0)
            self._bottom_centroid = bottom.mean(axis=0)
        self._fitted = True
        return self

    # -- transformation ---------------------------------------------------
    def transform(self, gaze: GazeVector, head: HeadPose) -> np.ndarray:
        """One ``float32`` feature row, ordered as :meth:`feature_names`."""
        if not self._fitted:
            raise RuntimeError(
                "CalibrationFeatureExtractor.transform() before fit(); calibrate first so the "
                "doc 5-1 centroids come from calibration data only"
            )
        raw = _raw_angles(gaze, head)
        if self.feature_set == "A":
            return raw[:2].astype(np.float32)
        if self.feature_set == "B":
            return raw.astype(np.float32)
        gaze_pair = raw[:2]
        return np.concatenate(
            [raw, gaze_pair - self._camera_centroid, gaze_pair - self._bottom_centroid]
        ).astype(np.float32)

    def transform_samples(self, samples: Sequence[CalibrationSample]) -> np.ndarray:
        """(N, n_features) design matrix; row order is preserved."""
        if len(samples) == 0:
            return np.zeros((0, self.n_features), dtype=np.float32)
        return np.vstack([self.transform(s.gaze, s.head_pose) for s in samples])

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Plain-data state for the doc 5-3 model file."""
        return {
            "feature_set": self.feature_set,
            "fitted": self._fitted,
            "camera_centroid": None
            if self._camera_centroid is None
            else [float(v) for v in self._camera_centroid],
            "bottom_centroid": None
            if self._bottom_centroid is None
            else [float(v) for v in self._bottom_centroid],
            "feature_names": self.feature_names(),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CalibrationFeatureExtractor":
        """Restore the frozen anchors exactly; never re-derive them from new data."""
        obj = cls(d["feature_set"])
        camera = d.get("camera_centroid")
        bottom = d.get("bottom_centroid")
        obj._camera_centroid = None if camera is None else np.asarray(camera, dtype=np.float64)
        obj._bottom_centroid = None if bottom is None else np.asarray(bottom, dtype=np.float64)
        obj._fitted = bool(d.get("fitted", True))
        if obj.requires_centroids and obj._fitted and (
            obj._camera_centroid is None or obj._bottom_centroid is None
        ):
            raise ValueError(
                f"feature set {obj.feature_set} was saved as fitted but carries no centroids"
            )
        return obj

    def __repr__(self) -> str:
        return (
            f"CalibrationFeatureExtractor(feature_set={self.feature_set!r}, "
            f"fitted={self._fitted}, n_features={self.n_features})"
        )
