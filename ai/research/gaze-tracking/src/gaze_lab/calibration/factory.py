"""Pick the per-user classifier named by ``calibration.method``.

Two families share one surface (``fit -> (classifier, quality)``,
``is_fitted``, ``quality``, ``classes``, ``decide``):

* ``reference`` -- anchors in gaze space (``calibration.references``), the
  shipped default: CAMERA / SCREEN / BOTTOM / OTHER.
* ``logistic`` -- the doc 5-3 per-user logistic regression
  (``calibration.classifier``): CAMERA / BOTTOM, kept for ablation.
"""

from __future__ import annotations

from typing import Optional, Protocol, Sequence, Tuple

from gaze_lab.config import CalibrationConfig
from gaze_lab.schemas import CalibrationQuality, CalibrationSample, FrameObservation, GazeDecision, GazeVector

CALIBRATION_METHODS: Tuple[str, ...] = ("reference", "logistic")


class GazeClassifier(Protocol):
    method: str

    @property
    def is_fitted(self) -> bool: ...

    @property
    def quality(self) -> Optional[CalibrationQuality]: ...

    @property
    def classes(self) -> Tuple[str, ...]: ...

    def decide(
        self, obs: FrameObservation, gaze: Optional[GazeVector], latency_ms: float = 0.0
    ) -> GazeDecision: ...


def normalise_method(name: str) -> str:
    """Canonical method name; an unknown one raises instead of defaulting."""
    key = str(name).strip().lower()
    if key not in CALIBRATION_METHODS:
        raise ValueError(f"unknown calibration method {name!r}; expected one of {CALIBRATION_METHODS}")
    return key


def fit_gaze_classifier(
    samples: Sequence[CalibrationSample], cfg: CalibrationConfig
) -> Tuple[GazeClassifier, CalibrationQuality]:
    """Fit the configured classifier and return it with its quality report."""
    method = normalise_method(cfg.method)
    if method == "reference":
        from gaze_lab.calibration.references import ReferenceAnchorClassifier

        return ReferenceAnchorClassifier.fit(samples, cfg)
    from gaze_lab.calibration.classifier import PerUserGazeClassifier

    return PerUserGazeClassifier.fit(samples, cfg)
