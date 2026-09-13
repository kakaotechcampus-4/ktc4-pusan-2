"""Per-user gaze classifier and the UNCERTAIN rule (doc 5-3, doc 5-4).

One logistic regression per user, trained on that user's own four seconds of
calibration and thrown away with the session.  It is small on purpose: with ~32
training rows anything with more capacity would memorise the calibration pose
instead of the CAMERA/BOTTOM distinction, and doc 3-3 wants the whole per-frame
budget under 125 ms anyway.
"""

from __future__ import annotations

import math
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import joblib
import numpy as np
import sklearn
from sklearn.pipeline import Pipeline

from vision.config import CalibrationConfig
from vision.schemas import (
    CalibrationQuality,
    CalibrationSample,
    FrameObservation,
    GazeDecision,
    GazeLabel,
    GazeState,
    GazeVector,
    HeadPose,
    InvalidReason,
)

from vision.calibration.features import CalibrationFeatureExtractor
from vision.calibration.quality import assess_calibration, build_calibration_pipeline, split_by_label

#: Bumped whenever the persisted payload changes shape.  ``load`` refuses a
#: mismatch: a stale model file scored against new features would produce
#: plausible-looking nonsense rather than an error.
SCHEMA_VERSION = "gaze_calib_v1"

#: doc 5-4 UNCERTAIN reasons that are not frame-level ``InvalidReason`` values.
UNCERTAIN_LOW_CONFIDENCE = "LOW_CONFIDENCE"
UNCERTAIN_LOW_MARGIN = "LOW_MARGIN"


class PerUserGazeClassifier:
    """CAMERA vs BOTTOM for one user (doc 5-3) plus the doc 5-4 decision rule."""

    def __init__(
        self,
        extractor: CalibrationFeatureExtractor,
        pipeline: Optional[Pipeline],
        cfg: CalibrationConfig,
        quality: Optional[CalibrationQuality] = None,
    ) -> None:
        self._extractor = extractor
        self._pipeline = pipeline
        self._cfg = cfg
        self._quality = quality
        self._i_camera, self._i_bottom = _class_indices(pipeline)

    # -- construction -----------------------------------------------------
    @classmethod
    def fit(
        cls, samples: Sequence[CalibrationSample], cfg: CalibrationConfig
    ) -> Tuple["PerUserGazeClassifier", CalibrationQuality]:
        """Fit the doc 5-3 pipeline and return it with its doc 5-2 quality report.

        The model is trained whenever both classes are present, even when the
        quality check says RETRY_REQUIRED: the retry policy belongs to the
        caller (the runtime shows the hint, the ablation in doc 23 wants the
        numbers either way).  Callers must therefore check ``quality.ok`` before
        trusting a decision -- ``fit`` never decides that for them.  When a class
        is missing entirely there is nothing to train, and the returned
        classifier reports ``is_fitted == False``.
        """
        extractor = CalibrationFeatureExtractor(cfg.feature_set)
        camera, bottom = split_by_label(samples)
        trainable = len(camera) >= 1 and len(bottom) >= 1
        if trainable:
            # Fitted before the assessment so the frozen anchors are the ones
            # the shipped model uses, not a second set computed by the check.
            extractor.fit(camera + bottom)

        quality = assess_calibration(samples, cfg, extractor)

        pipeline: Optional[Pipeline] = None
        if trainable:
            ordered = list(camera) + list(bottom)
            labels = [GazeLabel.CAMERA.value] * len(camera) + [GazeLabel.BOTTOM.value] * len(bottom)
            pipeline = build_calibration_pipeline(cfg)
            pipeline.fit(extractor.transform_samples(ordered), np.asarray(labels))
        return cls(extractor, pipeline, cfg, quality), quality

    # -- introspection ----------------------------------------------------
    @property
    def is_fitted(self) -> bool:
        return self._pipeline is not None

    @property
    def config(self) -> CalibrationConfig:
        return self._cfg

    @property
    def extractor(self) -> CalibrationFeatureExtractor:
        return self._extractor

    @property
    def quality(self) -> Optional[CalibrationQuality]:
        """The report from the calibration this model was fitted on, if any."""
        return self._quality

    def feature_names(self) -> List[str]:
        return self._extractor.feature_names()

    # -- inference --------------------------------------------------------
    def predict_proba(self, gaze: GazeVector, head: HeadPose) -> Tuple[float, float]:
        """``(p_camera, p_bottom)`` for one frame (doc 5-3).

        Uses the anchors frozen at calibration time; see the leakage note in
        ``features.CalibrationFeatureExtractor``.
        """
        if self._pipeline is None:
            raise RuntimeError(
                "PerUserGazeClassifier is not fitted; calibration produced "
                f"reason={None if self._quality is None else self._quality.reason}"
            )
        row = self._extractor.transform(gaze, head).reshape(1, -1)
        proba = self._pipeline.predict_proba(row)[0]
        return float(proba[self._i_camera]), float(proba[self._i_bottom])

    def decide(
        self,
        obs: FrameObservation,
        gaze: Optional[GazeVector],
        latency_ms: float = 0.0,
    ) -> GazeDecision:
        """Apply the doc 5-4 rule to one frame.

        Branch order is the document's: an unusable frame is UNCERTAIN with the
        preprocess reason attached (so doc 19 buckets can tell NO_FACE from
        EYES_CLOSED), then the confidence floor, then the margin floor.

        For a two-class model ``margin == 2 * p_max - 1``, so with the default
        thresholds (0.70 / 0.20) the confidence floor is the binding one and
        LOW_MARGIN only appears once a sweep (doc 19) raises it above
        ``2 * p_max_threshold - 1``.  Both branches stay because the sweep moves
        them independently.

        ``GazeDecision.face_valid`` answers "was this frame usable for a
        decision", so a valid face whose backbone returned nothing is False with
        ``uncertain_reason=BACKBONE_FAILED``.  Reading it as "a face was seen"
        instead would let a dead backbone hold the smoother (doc 6) in its last
        state forever; the reason field, not the flag, is what distinguishes the
        two for the UI.
        """
        if not obs.face_valid or gaze is None or not _finite_gaze(gaze):
            reason = obs.invalid_reason
            if reason is None:
                reason = (
                    InvalidReason.BACKBONE_FAILED.value
                    if obs.face_valid
                    else InvalidReason.NO_FACE.value
                )
            return GazeDecision(
                t_ms=obs.t_ms,
                frame_id=obs.frame_id,
                label=GazeState.UNCERTAIN.value,
                p_camera=0.5,
                p_bottom=0.5,
                face_valid=False,
                uncertain_reason=reason,
                gaze=gaze,
                latency_ms=latency_ms,
            )

        p_camera, p_bottom = self.predict_proba(gaze, obs.head_pose)
        decision = GazeDecision(
            t_ms=obs.t_ms,
            frame_id=obs.frame_id,
            label=GazeState.UNCERTAIN.value,
            p_camera=p_camera,
            p_bottom=p_bottom,
            face_valid=True,
            gaze=gaze,
            latency_ms=latency_ms,
        )
        if decision.p_max < float(self._cfg.p_max_threshold):
            decision.uncertain_reason = UNCERTAIN_LOW_CONFIDENCE
        elif decision.margin < float(self._cfg.margin_threshold):
            decision.uncertain_reason = UNCERTAIN_LOW_MARGIN
        else:
            decision.label = (
                GazeState.CAMERA.value if p_camera >= p_bottom else GazeState.BOTTOM.value
            )
        return decision

    # -- persistence ------------------------------------------------------
    def save(self, path: Union[str, Path]) -> Path:
        """Persist model + extractor state + config under a schema version.

        The extractor state travels with the estimator because the frozen
        centroids are part of the model: reloading them from anywhere else would
        change what the coefficients mean.
        """
        if self._pipeline is None:
            raise RuntimeError("refusing to save an unfitted PerUserGazeClassifier")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "pipeline": self._pipeline,
            "extractor": self._extractor.to_dict(),
            "feature_names": self.feature_names(),
            "config": asdict(self._cfg),
            "quality": None if self._quality is None else asdict(self._quality),
            #: Recorded for forensics only; a minor sklearn difference still loads.
            "sklearn_version": sklearn.__version__,
        }
        joblib.dump(payload, target)
        return target

    @classmethod
    def load(cls, path: Union[str, Path]) -> "PerUserGazeClassifier":
        """Restore a saved model, rejecting a foreign or stale schema version."""
        payload = joblib.load(Path(path))
        if not isinstance(payload, dict):
            raise ValueError(f"{path} is not a PerUserGazeClassifier payload")
        version = payload.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"calibration model schema mismatch: file has {version!r}, "
                f"this build expects {SCHEMA_VERSION!r}; recalibrate the user"
            )
        extractor = CalibrationFeatureExtractor.from_dict(payload["extractor"])
        saved_names = list(payload.get("feature_names") or [])
        if saved_names and saved_names != extractor.feature_names():
            raise ValueError(
                f"saved feature names {saved_names} do not match feature set "
                f"{extractor.feature_set}; recalibrate the user"
            )
        cfg = _build_config(payload.get("config") or {})
        quality_data = payload.get("quality")
        quality = None if quality_data is None else CalibrationQuality(**quality_data)
        return cls(extractor, payload["pipeline"], cfg, quality)

    def __repr__(self) -> str:
        return (
            f"PerUserGazeClassifier(feature_set={self._extractor.feature_set!r}, "
            f"fitted={self.is_fitted}, "
            f"status={None if self._quality is None else self._quality.status})"
        )


def _finite_gaze(gaze: GazeVector) -> bool:
    return math.isfinite(gaze.gaze_yaw) and math.isfinite(gaze.gaze_pitch)


def _class_indices(pipeline: Optional[Pipeline]) -> Tuple[int, int]:
    """Column positions of CAMERA and BOTTOM in ``predict_proba`` output.

    sklearn sorts class labels, which puts BOTTOM first; reading the order off
    the fitted estimator keeps a silent label swap impossible.
    """
    if pipeline is None:
        return 0, 1
    classes = [str(c) for c in pipeline.classes_]
    return classes.index(GazeLabel.CAMERA.value), classes.index(GazeLabel.BOTTOM.value)


def _build_config(data: Dict[str, Any]) -> CalibrationConfig:
    known = {f.name for f in fields(CalibrationConfig)}
    return CalibrationConfig(**{k: v for k, v in data.items() if k in known})
