"""Offline gaze evaluation (doc 7, doc 19) -> ``ai/reports/<run>/gaze_eval.{json,md}``.

This is the harness doc 20 asks for: it runs on the *feature table*, not on
video.  Every row already carries the backbone output and the head pose, so the
only thing left to re-run offline is the per-user part of the pipeline --
calibration (doc 5-1..5-3), the UNCERTAIN rule (doc 5-4) and the temporal
smoother (doc 6).  Re-running them here rather than trusting the ``pred_label``
column is what makes a threshold change, a feature-set change or a smoothing
change measurable without touching a camera.

Protocol, per participant, straight out of doc 4-3::

    calibration_frames()   first calibration block only  -> fit
    evaluation_frames()    everything after that block   -> score

``vision.data.splits.per_participant_partition`` enforces that those two sets
never intersect, so leakage fails the run instead of inflating it.

What the report contains and why each piece is separate:

* frame metrics before and after smoothing -- doc 6 trades latency for
  stability, and only the pair shows what it bought;
* per-user metrics -- doc 7 gates the *minimum*, because one destroyed
  calibration is invisible in a pooled mean;
* doc 19 buckets -- where the failures live;
* the pitch-ordering sanity check -- a flipped adapter sign (schemas.py) that
  still scores well;
* latency, with its source stated per frame, because a number nobody measured
  must not read as a pass.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


def _bootstrap_import_path() -> None:
    """See ``metrics._bootstrap_import_path``; repeated so any entry point works."""
    root = Path(__file__).resolve().parents[2]
    for entry in (root / "ai" / "src", root / "ai"):
        text = str(entry)
        if text not in sys.path:
            sys.path.insert(0, text)


_bootstrap_import_path()

from vision.calibration.classifier import PerUserGazeClassifier  # noqa: E402
from vision.config import REPORTS_DIR, VisionConfig, load_config  # noqa: E402
from vision.data.features_table import load_table, save_table  # noqa: E402
from vision.data.labels import load_segments  # noqa: E402
from vision.data.splits import participant_split, per_participant_partition  # noqa: E402
from vision.schemas import (  # noqa: E402
    CalibrationQuality,
    CalibrationSample,
    FrameObservation,
    FrameQuality,
    GazeDecision,
    GazeState,
    GazeStateEvent,
    GazeVector,
    HeadPose,
)
from vision.temporal.smoother import TemporalSmoother  # noqa: E402

from evaluation import buckets as buckets_module  # noqa: E402
from evaluation import sanity as sanity_module  # noqa: E402
from evaluation.metrics import (  # noqa: E402
    CLASSES,
    UNCERTAIN,
    evaluate_release_gate,
    frame_metrics,
    latency_stats,
    per_user_metrics,
    per_user_summary,
    segment_metrics_from_predictions,
    segment_predictions,
)

#: Columns this harness writes onto the evaluation rows.  ``pred_label`` and
#: friends deliberately reuse the feature-table names so the bucket report and
#: the metrics read the same column whether the predictions were refitted here
#: or loaded from the table; ``predictions.source`` in the JSON says which.
PREDICTION_COLUMNS: Tuple[str, ...] = (
    "pred_label",
    "p_camera",
    "p_bottom",
    "uncertain_reason",
    "decision_face_valid",
    "decide_ms",
    "eval_latency_ms",
    "latency_source",
    "smoothed_label",
    "smoothed_confidence",
)


# --------------------------------------------------------------------------
# Rehydration: feature-table row -> the objects doc 5 works on
# --------------------------------------------------------------------------


def _cell(row: Mapping[str, Any], column: str) -> Any:
    value = row.get(column, None)
    if value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _truthy(value: Any) -> bool:
    """``bool(value)`` that survives ``pd.NA`` (which raises on truth-testing)."""
    if value is None or value is pd.NA:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    return bool(value)


def _float(row: Mapping[str, Any], column: str, default: float = 0.0) -> float:
    value = _cell(row, column)
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def observation_from_row(row: Mapping[str, Any]) -> FrameObservation:
    """Rebuild the pixel-free part of a ``FrameObservation`` (schemas.py).

    The crops stay ``None`` -- doc 20 keeps angles, not faces -- which is fine
    because everything downstream of the backbone reads only the head pose, the
    validity flag and the timestamps.
    """
    quality = FrameQuality(
        face_area_ratio=_float(row, "q_face_area_ratio"),
        face_brightness=_float(row, "q_face_brightness"),
        background_brightness=_float(row, "q_background_brightness"),
        face_contrast=_float(row, "q_face_contrast"),
        left_eye_openness=_float(row, "q_left_eye_openness"),
        right_eye_openness=_float(row, "q_right_eye_openness"),
        landmark_visibility=_float(row, "q_landmark_visibility", 1.0),
        touches_border=_truthy(_cell(row, "q_touches_border")),
    )
    invalid_reason = _cell(row, "invalid_reason")
    return FrameObservation(
        frame_id=int(_float(row, "frame_id")),
        t_ms=int(_float(row, "t_ms")),
        face_confidence=_float(row, "face_confidence"),
        face_valid=_truthy(_cell(row, "face_valid")),
        head_pose=HeadPose(
            yaw=_float(row, "head_yaw"),
            pitch=_float(row, "head_pitch"),
            roll=_float(row, "head_roll"),
            reprojection_error=_float(row, "head_reprojection_error"),
            depth_proxy=_float(row, "head_depth_proxy"),
        ),
        quality=quality,
        invalid_reason=None if invalid_reason is None else str(invalid_reason),
        preprocess_ms=_float(row, "preprocess_ms"),
    )


def gaze_from_row(row: Mapping[str, Any]) -> Optional[GazeVector]:
    """The backbone output stored in the row, or ``None`` when it has none.

    ``None`` and not a zeroed vector: doc 5-4 branches on "the backbone produced
    nothing", and a (0, 0) gaze is a perfectly plausible measurement.
    """
    yaw = _cell(row, "gaze_yaw")
    pitch = _cell(row, "gaze_pitch")
    if yaw is None or pitch is None:
        return None
    yaw, pitch = float(yaw), float(pitch)
    if not (math.isfinite(yaw) and math.isfinite(pitch)):
        return None
    backbone = _cell(row, "backbone")
    return GazeVector(
        gaze_yaw=yaw,
        gaze_pitch=pitch,
        confidence=_float(row, "gaze_confidence"),
        backbone="unknown" if backbone is None else str(backbone),
        inference_ms=_float(row, "inference_ms"),
    )


def calibration_samples_from_frame(df: pd.DataFrame) -> Tuple[List[CalibrationSample], int]:
    """``(samples, n_unusable)`` for one participant's calibration block.

    Frames without a gaze vector are counted, not silently dropped: "the
    calibration failed because the backbone produced nothing" and "the classes
    overlap" are different problems with different fixes (doc 5-2).
    """
    samples: List[CalibrationSample] = []
    unusable = 0
    for row in df.to_dict(orient="records"):
        label = str(_cell(row, "label") or "").strip().upper()
        if label not in CLASSES:
            continue
        gaze = gaze_from_row(row)
        if gaze is None:
            unusable += 1
            continue
        observation = observation_from_row(row)
        samples.append(
            CalibrationSample(
                label=label,
                gaze=gaze,
                head_pose=observation.head_pose,
                t_ms=observation.t_ms,
                frame_id=observation.frame_id,
            )
        )
    return samples, unusable


# --------------------------------------------------------------------------
# The doc 5-4 rule, vectorised (for the sweep) and cross-checked
# --------------------------------------------------------------------------


def apply_uncertain_rule(
    p_camera: Sequence[float],
    p_bottom: Sequence[float],
    face_valid: Sequence[Any],
    p_max_threshold: float,
    margin_threshold: float,
) -> np.ndarray:
    """Vectorised doc 5-4 decision rule over stored probabilities.

    It exists for ``threshold_sweep``, which re-decides the same frames a few
    hundred times and cannot afford to rebuild a ``FrameObservation`` each time.
    It is a second implementation of ``PerUserGazeClassifier.decide``, so
    :func:`run_evaluation` asserts the two agree on every frame at the
    configured thresholds and records the result in the report.

    ``face_valid`` is the *decision's* flag, not the frame's: doc 5-4 abstains
    when the backbone returned nothing even though the face was fine.
    """
    camera = pd.to_numeric(pd.Series(list(p_camera)), errors="coerce").to_numpy(
        dtype=float, na_value=np.nan
    )
    bottom = pd.to_numeric(pd.Series(list(p_bottom)), errors="coerce").to_numpy(
        dtype=float, na_value=np.nan
    )
    valid = np.asarray([_truthy(value) for value in face_valid], dtype=bool)
    valid &= np.isfinite(camera) & np.isfinite(bottom)

    p_max = np.maximum(camera, bottom)
    margin = np.abs(camera - bottom)
    decided = valid & (p_max >= float(p_max_threshold)) & (margin >= float(margin_threshold))
    labels = np.where(camera >= bottom, GazeState.CAMERA.value, GazeState.BOTTOM.value)
    return np.where(decided, labels, UNCERTAIN)


# --------------------------------------------------------------------------
# Per-participant scoring
# --------------------------------------------------------------------------


@dataclass
class ParticipantResult:
    """Everything one participant contributes to the report."""

    participant_id: str
    n_calibration: int
    n_calibration_unusable: int
    quality: Optional[CalibrationQuality]
    fitted: bool
    rows: pd.DataFrame
    events: List[GazeStateEvent] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def calibration_failed(self) -> bool:
        """doc 5-2: a RETRY_REQUIRED or an unfittable calibration both count."""
        return (not self.fitted) or (self.quality is None) or (not self.quality.ok)


def _resolve_latency(row: Mapping[str, Any], decide_ms: float) -> Tuple[float, str]:
    """Per-frame end-to-end latency and where the number came from.

    ``latency_ms`` written by the extractor is the real end-to-end measurement
    and always wins.  Without it the sum of the three measured stages is used
    and labelled ``sum_of_stages`` -- each part was timed, just not in the same
    process, so it is an estimate and the report says so.  When neither exists
    the frame contributes NaN and is dropped from the statistics rather than
    filled in.
    """
    stored = _float(row, "latency_ms", math.nan)
    if math.isfinite(stored) and stored > 0.0:
        return stored, "table"
    preprocess = _cell(row, "preprocess_ms")
    inference = _cell(row, "inference_ms")
    if preprocess is None and inference is None:
        return math.nan, "unmeasured"
    total = _float(row, "preprocess_ms") + _float(row, "inference_ms") + float(decide_ms)
    return total, "sum_of_stages"


def score_participant(
    participant_id: str,
    calibration_df: pd.DataFrame,
    evaluation_df: pd.DataFrame,
    cfg: VisionConfig,
    *,
    smooth: bool = True,
) -> ParticipantResult:
    """Fit this user's classifier on their calibration block and score the rest.

    Fitting proceeds even when ``quality.ok`` is False (doc 5-2 leaves the retry
    policy to the caller) so the report can show what a bad calibration costs;
    ``calibration_failure_rate`` counts it either way, and
    ``--drop-failed-calibration`` removes those participants from the metrics.
    """
    samples, unusable = calibration_samples_from_frame(calibration_df)
    rows = evaluation_df.copy()

    try:
        classifier, quality = PerUserGazeClassifier.fit(samples, cfg.calibration)
    except Exception as exc:  # a broken calibration must not abort the whole run
        for column in PREDICTION_COLUMNS:
            rows[column] = None
        rows["pred_label"] = UNCERTAIN
        return ParticipantResult(
            participant_id=participant_id,
            n_calibration=len(samples),
            n_calibration_unusable=unusable,
            quality=None,
            fitted=False,
            rows=rows,
            error=f"{type(exc).__name__}: {exc}",
        )

    records = rows.to_dict(orient="records")
    decisions: List[GazeDecision] = []
    latencies: List[float] = []
    sources: List[str] = []
    decide_times: List[float] = []

    for record in records:
        observation = observation_from_row(record)
        gaze = gaze_from_row(record)
        started = time.perf_counter()
        if classifier.is_fitted:
            decision = classifier.decide(observation, gaze)
        else:
            decision = GazeDecision(
                t_ms=observation.t_ms,
                frame_id=observation.frame_id,
                label=UNCERTAIN,
                p_camera=0.5,
                p_bottom=0.5,
                face_valid=False,
                uncertain_reason=observation.invalid_reason or "NO_CALIBRATION",
                gaze=gaze,
            )
        decide_ms = (time.perf_counter() - started) * 1000.0
        latency, source = _resolve_latency(record, decide_ms)
        decision.latency_ms = 0.0 if math.isnan(latency) else latency
        decisions.append(decision)
        decide_times.append(decide_ms)
        latencies.append(latency)
        sources.append(source)

    rows["pred_label"] = [decision.label for decision in decisions]
    rows["p_camera"] = [decision.p_camera for decision in decisions]
    rows["p_bottom"] = [decision.p_bottom for decision in decisions]
    rows["uncertain_reason"] = [decision.uncertain_reason for decision in decisions]
    rows["decision_face_valid"] = [decision.face_valid for decision in decisions]
    rows["decide_ms"] = decide_times
    rows["eval_latency_ms"] = latencies
    rows["latency_source"] = sources

    events: List[GazeStateEvent] = []
    if smooth:
        # The smoother is stateful and needs the stream in recording order, but
        # the report rows must stay in table order; walk a sorted index and put
        # each event back in its own row's slot rather than joining on t_ms,
        # which two rows of a merged table can share.
        smoother = TemporalSmoother(cfg.temporal)
        order = sorted(
            range(len(decisions)), key=lambda i: (decisions[i].t_ms, decisions[i].frame_id)
        )
        per_row: List[Optional[GazeStateEvent]] = [None] * len(decisions)
        for index in order:
            event = smoother.update(decisions[index])
            per_row[index] = event
            events.append(event)
        rows["smoothed_label"] = [event.label for event in per_row]
        rows["smoothed_confidence"] = [event.confidence for event in per_row]
    else:
        rows["smoothed_label"] = None
        rows["smoothed_confidence"] = None

    return ParticipantResult(
        participant_id=participant_id,
        n_calibration=len(samples),
        n_calibration_unusable=unusable,
        quality=quality,
        fitted=classifier.is_fitted,
        rows=rows,
        events=events,
    )


# --------------------------------------------------------------------------
# Run assembly
# --------------------------------------------------------------------------


def _stored_prediction_rows(evaluation_df: pd.DataFrame) -> pd.DataFrame:
    """Use the table's own ``pred_label`` column instead of refitting."""
    rows = evaluation_df.copy()
    if "pred_label" not in rows.columns:
        raise ValueError(
            "--use-stored-predictions needs a pred_label column; this table was written "
            "before any classifier ran"
        )
    rows["decision_face_valid"] = rows.get("face_valid", True)
    rows["decide_ms"] = math.nan
    rows["eval_latency_ms"] = rows["latency_ms"] if "latency_ms" in rows.columns else math.nan
    rows["latency_source"] = "table"
    rows["smoothed_label"] = None
    rows["smoothed_confidence"] = None
    return rows


def _calibration_record(result: ParticipantResult) -> Dict[str, Any]:
    quality = result.quality
    return {
        "participant_id": result.participant_id,
        "n_calibration_samples": result.n_calibration,
        "n_calibration_unusable": result.n_calibration_unusable,
        "fitted": result.fitted,
        "status": None if quality is None else quality.status,
        "reason": None if quality is None else quality.reason,
        "hint": None if quality is None else quality.hint,
        "loo_accuracy": None if quality is None else quality.loo_accuracy,
        "separability": None if quality is None else quality.separability,
        "centroid_distance": None if quality is None else quality.centroid_distance,
        "n_camera": None if quality is None else quality.n_camera,
        "n_bottom": None if quality is None else quality.n_bottom,
        "failed": result.calibration_failed,
        "error": result.error,
    }


def run_evaluation(
    df: pd.DataFrame,
    cfg: VisionConfig,
    *,
    smooth: bool = True,
    use_stored_predictions: bool = False,
    drop_failed_calibration: bool = False,
    segments: Optional[Sequence[Any]] = None,
    thresholds: Optional[buckets_module.BucketThresholds] = None,
) -> Dict[str, Any]:
    """Run the doc 4-3 protocol over every participant and score it (doc 7).

    Returns the report dict that :func:`write_report` serialises; the scored
    frames themselves are under ``"frames"`` so a caller can dump or re-slice
    them without running everything again.
    """
    partition = per_participant_partition(df, cfg)
    results: List[ParticipantResult] = []
    for participant_id, parts in partition.items():
        evaluation_df = parts["evaluation"]
        if evaluation_df.empty:
            continue
        if use_stored_predictions:
            results.append(
                ParticipantResult(
                    participant_id=participant_id,
                    n_calibration=int(len(parts["calibration"])),
                    n_calibration_unusable=0,
                    quality=None,
                    fitted=True,
                    rows=_stored_prediction_rows(evaluation_df),
                )
            )
            continue
        results.append(
            score_participant(
                participant_id, parts["calibration"], evaluation_df, cfg, smooth=smooth
            )
        )

    if not results:
        raise ValueError("no participant produced evaluation frames; check the label column")

    scored = pd.concat([result.rows for result in results], ignore_index=True)
    failed_ids = [result.participant_id for result in results if result.calibration_failed]
    if drop_failed_calibration and failed_ids:
        scored = scored[~scored["participant_id"].isin(failed_ids)]
        if scored.empty:
            raise ValueError(
                "every participant failed calibration (doc 5-2); nothing left to score with "
                "--drop-failed-calibration"
            )

    # doc 5-4 is implemented twice (object-wise in the classifier, vectorised
    # here for the sweep); prove they agree instead of assuming it.
    rule_consistent: Optional[bool] = None
    if not use_stored_predictions and len(scored):
        replayed = apply_uncertain_rule(
            scored["p_camera"],
            scored["p_bottom"],
            scored["decision_face_valid"],
            cfg.calibration.p_max_threshold,
            cfg.calibration.margin_threshold,
        )
        rule_consistent = bool(
            (replayed.astype(object) == scored["pred_label"].to_numpy(dtype=object)).all()
        )

    bucketed = buckets_module.assign_buckets(scored, thresholds)
    frame = frame_metrics(
        bucketed["label"],
        bucketed["pred_label"],
        uncertain_reasons=bucketed.get("uncertain_reason", pd.Series([None] * len(bucketed))),
    )
    smoothed_frame = None
    if smooth and not use_stored_predictions and bucketed["smoothed_label"].notna().any():
        smoothed_frame = frame_metrics(bucketed["label"], bucketed["smoothed_label"])

    per_user = per_user_metrics(bucketed, latency_col="eval_latency_ms")
    latency = latency_stats(bucketed["eval_latency_ms"])
    n_participants = len(results)

    segment_report = None
    if segments:
        records: List[Dict[str, Any]] = []
        sources: set = set()
        n_given = 0
        by_participant: Dict[str, List[Any]] = {}
        for segment in segments:
            by_participant.setdefault(str(segment.participant_id), []).append(segment)
        for result in results:
            own = by_participant.get(str(result.participant_id))
            if not own:
                continue
            n_given += sum(1 for segment in own if segment.label in CLASSES)
            if result.events:
                stream: Any = result.events
                sources.add("smoothed_events")
            else:
                # No smoother in this run: score the raw per-frame decisions as
                # a state stream instead of skipping the segment metrics, and
                # say so -- the two are not comparable numbers.
                stream = list(
                    zip(result.rows["t_ms"].tolist(), result.rows["pred_label"].tolist())
                )
                sources.add("per_frame_decisions")
            records.extend(segment_predictions(stream, own))
        if records:
            segment_report = segment_metrics_from_predictions(records)
            segment_report["source"] = sorted(sources)
            # The gap is the calibration block and anything else outside the
            # evaluation window; reported so the drop is visible, not implied.
            segment_report["n_segments_in_label_file"] = n_given

    summary: Dict[str, Any] = {
        "config": {
            "hash": cfg.hash(),
            "feature_set": cfg.calibration.feature_set,
            "p_max_threshold": cfg.calibration.p_max_threshold,
            "margin_threshold": cfg.calibration.margin_threshold,
            "analysis_fps": cfg.preprocess.analysis_fps,
            "temporal": {
                "enabled": bool(smooth and not use_stored_predictions),
                "to_bottom_dwell_ms": cfg.temporal.to_bottom_dwell_ms,
                "to_camera_dwell_ms": cfg.temporal.to_camera_dwell_ms,
            },
        },
        "predictions": {
            "source": "table" if use_stored_predictions else "refit",
            "decision_rule_consistent": rule_consistent,
        },
        "dataset": {
            "n_rows_input": int(len(df)),
            "n_rows_scored": int(len(bucketed)),
            "participants": sorted({str(pid) for pid in bucketed["participant_id"]}),
            "backbones": sorted({str(b) for b in bucketed.get("backbone", pd.Series(dtype=object)).dropna().unique()}),
            "label_counts": {
                str(key): int(value) for key, value in bucketed["label"].value_counts().items()
            },
        },
        "calibration": {
            "n_participants": n_participants,
            "n_failed": len(failed_ids),
            "failure_rate": (len(failed_ids) / n_participants) if n_participants else 0.0,
            "failed_participants": failed_ids,
            "dropped_failed_from_metrics": bool(drop_failed_calibration),
            "per_participant": [_calibration_record(result) for result in results],
        },
        "frame": frame,
        "frame_smoothed": smoothed_frame,
        "per_user": per_user.attrs.get("summary", per_user_summary(per_user)),
        "per_user_rows": per_user.to_dict(orient="records"),
        "latency": latency,
        "latency_sources": {
            str(key): int(value)
            for key, value in bucketed["latency_source"].value_counts().items()
        },
        "buckets": buckets_module.bucket_report(bucketed, thresholds).to_dict(orient="records"),
        "bucket_thresholds": (thresholds or buckets_module.DEFAULT_THRESHOLDS).to_dict(),
        "sanity": {
            "pitch_ordering": sanity_module.pitch_ordering_report(bucketed).to_dict(
                orient="records"
            ),
            "uncertain_reasons": sanity_module.uncertain_reason_report(bucketed).to_dict(
                orient="records"
            ),
        },
        "segments": segment_report,
    }
    summary["sanity"]["pitch_ordering_summary"] = sanity_module.pitch_ordering_summary(
        pd.DataFrame(summary["sanity"]["pitch_ordering"])
    )
    summary["release_gate"] = evaluate_release_gate(summary, cfg.release_gate)
    summary["frames"] = bucketed
    return summary


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def jsonable(value: Any) -> Any:
    """Recursively convert numpy / pandas scalars so ``json`` can write them."""
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (np.ndarray,)):
        return [jsonable(item) for item in value.tolist()]
    if isinstance(value, pd.DataFrame):
        return jsonable(value.to_dict(orient="records"))
    return str(value)


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None or value is pd.NA:
        return "-"
    if isinstance(value, bool):
        return "PASS" if value else "FAIL"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return "-" if not math.isfinite(number) else f"{number:.{digits}f}"
    return str(value)


def md_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str], digits: int = 4) -> str:
    """Markdown table without pulling in ``tabulate``.

    Cell text is pipe-escaped: a rule string like ``abs(x) | y`` would otherwise
    silently split a row into extra columns.
    """
    if not rows:
        return "_(no rows)_\n"

    def cell(row: Mapping[str, Any], column: str) -> str:
        return _fmt(row.get(column), digits).replace("|", "\\|")

    header = "| " + " | ".join(columns) + " |"
    rule = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(cell(row, column) for column in columns) + " |" for row in rows
    ]
    return "\n".join([header, rule, *body]) + "\n"


def _smoothing_caveat(report: Mapping[str, Any]) -> List[str]:
    """Warn that the smoothed row is not comparable to the per-frame row above it.

    doc 6 buys stability with latency: a transition only commits after
    ``to_*_dwell_ms`` of sustained evidence, so every frame between the true cue
    change and the commit is charged to the smoothed row as a misclassification.
    That is the smoother working, not failing -- but the two rows sit in one
    table, which invites exactly the wrong reading ("smoothing cost us 20 points
    of macro F1").  doc 4-2's guard band is only +/-``transition_guard_ms``
    (500 ms shipped) and cannot absorb a 600-1000 ms dwell, so the effect does
    not wash out however clean the data is.

    The lens that scores a smoothed stream correctly is the segment level, where
    the lag is reported as ``onset_latency_ms`` instead of being counted as
    error.  Point at it, and echo its headline numbers when they exist.
    """
    smoothed = report.get("frame_smoothed")
    if not smoothed:
        return []

    lines = [
        "> **The smoothed row is not comparable to the per-frame rows above it.**",
        "> doc 6 commits a transition only after `to_bottom_dwell_ms` / `to_camera_dwell_ms`",
        "> of sustained evidence, so every frame between the real cue change and the commit",
        "> is counted here as a misclassification. That latency is the smoother working as",
        "> designed; doc 4-2's +/-`transition_guard_ms` guard band is narrower than the dwell",
        "> and cannot absorb it. Score the smoothed stream at the **segment level**, where the",
        "> same lag is reported as `onset_latency_ms` rather than charged as error.",
    ]

    segments = report.get("segments") or {}
    onset = segments.get("onset_latency_ms") or {}
    if segments.get("macro_f1") is not None:
        detection = segments.get("detection_rate") or {}
        lines.append(">")
        lines.append(
            f"> On this run the segment level reads macro F1 "
            f"{segments['macro_f1']:.4f} over {segments.get('n_segments', 0)} segments "
            f"(BOTTOM detection rate {_fmt_ratio(detection.get('BOTTOM'))}, "
            f"CAMERA {_fmt_ratio(detection.get('CAMERA'))}), with onset latency p50 "
            f"BOTTOM {_fmt_ms(onset.get('BOTTOM'))} / CAMERA {_fmt_ms(onset.get('CAMERA'))}."
        )
    else:
        lines.append(">")
        lines.append(
            "> No segment labels were passed, so that lens is missing from this run -- "
            "re-run with `--segments <labels>.segments.json` to measure onset latency "
            "instead of inferring it from the drop above."
        )
    return ["", *lines, ""]


def _fmt_ratio(value: Any) -> str:
    return "-" if value is None else f"{float(value):.4f}"


def _fmt_ms(stats: Any) -> str:
    if not isinstance(stats, Mapping) or stats.get("p50") is None:
        return "-"
    return f"{float(stats['p50']):.0f} ms"


def render_markdown(report: Mapping[str, Any], run_name: str) -> str:
    """The human-readable half of the report (doc 7 / doc 18 review material)."""
    frame = report["frame"]
    gate = report["release_gate"]
    lines: List[str] = [
        f"# gaze_eval - {run_name}",
        "",
        f"- config hash: `{report['config']['hash']}`  feature set: "
        f"`{report['config']['feature_set']}`",
        f"- predictions: `{report['predictions']['source']}`  "
        f"doc 5-4 rule cross-check: `{report['predictions']['decision_rule_consistent']}`",
        f"- participants: {len(report['dataset']['participants'])}  "
        f"scored frames: {report['dataset']['n_rows_scored']}  "
        f"backbones: {', '.join(report['dataset']['backbones']) or '-'}",
        "",
        "## Release gate (doc 7)",
        "",
        f"**{'PASSED' if gate['passed'] else 'FAILED'}**"
        + (f" - not measured: {', '.join(gate['missing'])}" if gate["missing"] else ""),
        "",
        md_table(gate["rows"], ["metric", "value", "target", "direction", "pass"]),
        "## Frame metrics (doc 7)",
        "",
        "Accuracy figures are over **decided** frames; UNCERTAIN is reported "
        "separately and never counted as a wrong class. "
        "`*_uncertain_as_error` is the pessimistic reading.",
        "",
        md_table(
            [
                {
                    "view": "per frame",
                    "macro_f1": frame["macro_f1"],
                    "bottom_recall": frame["bottom_recall"],
                    "camera_recall": frame["camera_recall"],
                    "accuracy": frame["accuracy"],
                    "uncertain_ratio": frame["uncertain_ratio"],
                    "n_labelled": frame["n_labelled"],
                },
                {
                    "view": "per frame, UNCERTAIN as error",
                    "macro_f1": frame["macro_f1_uncertain_as_error"],
                    "bottom_recall": frame["bottom_recall_uncertain_as_error"],
                    "camera_recall": frame["per_class_uncertain_as_error"]["CAMERA"]["recall"],
                    "accuracy": None,
                    "uncertain_ratio": frame["uncertain_ratio"],
                    "n_labelled": frame["n_labelled"],
                },
            ]
            + (
                [
                    {
                        "view": "after temporal smoothing (doc 6)",
                        "macro_f1": report["frame_smoothed"]["macro_f1"],
                        "bottom_recall": report["frame_smoothed"]["bottom_recall"],
                        "camera_recall": report["frame_smoothed"]["camera_recall"],
                        "accuracy": report["frame_smoothed"]["accuracy"],
                        "uncertain_ratio": report["frame_smoothed"]["uncertain_ratio"],
                        "n_labelled": report["frame_smoothed"]["n_labelled"],
                    }
                ]
                if report.get("frame_smoothed")
                else []
            ),
            [
                "view",
                "macro_f1",
                "bottom_recall",
                "camera_recall",
                "accuracy",
                "uncertain_ratio",
                "n_labelled",
            ],
        ),
        *_smoothing_caveat(report),
        "### Confusion (rows = truth, columns = prediction)",
        "",
        md_table(
            [
                {"truth": truth, **{key: value for key, value in row.items()}}
                for truth, row in frame["confusion"].items()
            ],
            ["truth", "CAMERA", "BOTTOM", "UNCERTAIN"],
            digits=0,
        ),
        "### Why frames were UNCERTAIN",
        "",
        md_table(report["sanity"]["uncertain_reasons"], ["reason", "n", "ratio_of_all_frames"]),
        "## Per user (doc 7 gates the minimum)",
        "",
        md_table(
            report["per_user_rows"],
            [
                "participant_id",
                "n_labelled",
                "macro_f1",
                "bottom_recall",
                "uncertain_ratio",
                "latency_p95_ms",
            ],
        ),
        "## Latency (doc 3-3 budget: 125 ms per frame)",
        "",
        md_table([report["latency"]], ["n", "p50", "p95", "p99", "mean", "max"], digits=2),
        f"Sources: {report['latency_sources']}. `sum_of_stages` = preprocess + inference + "
        "classifier, each measured but not in one process; `unmeasured` frames are dropped.",
        "",
        "## doc 19 failure buckets",
        "",
        "Two buckets are defined against the ground-truth label and therefore hold "
        "one class only (`n_camera` or `n_bottom` = 0); their macro F1 is capped at "
        "0.5 by construction, so read the recall of the class they contain instead.",
        "",
        md_table(
            report["buckets"],
            [
                "bucket",
                "n",
                "n_camera",
                "n_bottom",
                "macro_f1",
                "macro_f1_delta",
                "camera_recall",
                "bottom_recall",
                "uncertain_ratio",
            ],
        ),
        md_table(
            buckets_module.bucket_rules_table().to_dict(orient="records"),
            ["bucket", "doc19", "rule"],
        ),
        "## Sign-convention sanity (schemas.py)",
        "",
        "CAMERA gaze pitch must be **above** BOTTOM, i.e. `delta_gaze_pitch > 0`.",
        "",
        md_table(
            report["sanity"]["pitch_ordering"],
            [
                "participant_id",
                "backbone",
                "n_camera",
                "n_bottom",
                "mean_gaze_pitch_camera",
                "mean_gaze_pitch_bottom",
                "delta_gaze_pitch",
                "effect_size",
                "verdict",
            ],
        ),
        "## Calibration (doc 5-2)",
        "",
        f"failure rate: {_fmt(report['calibration']['failure_rate'])} "
        f"({report['calibration']['n_failed']}/{report['calibration']['n_participants']})",
        "",
        md_table(
            report["calibration"]["per_participant"],
            [
                "participant_id",
                "n_calibration_samples",
                "status",
                "reason",
                "loo_accuracy",
                "separability",
                "centroid_distance",
            ],
        ),
    ]

    if report.get("segments"):
        segments = report["segments"]
        lines += [
            "## Segment level (doc 7)",
            "",
            f"state stream: `{', '.join(segments.get('source', []))}`; "
            f"{segments['n_segments']} of {segments.get('n_segments_in_label_file')} labelled "
            "segments overlap the evaluation window (the rest is the calibration block).",
            "",
            md_table(
                [
                    {
                        "n_segments": segments["n_segments"],
                        "macro_f1": segments["macro_f1"],
                        "bottom_recall": segments["bottom_recall"],
                        "segment_uncertain_ratio": segments["segment_uncertain_ratio"],
                        "time_accuracy": segments["time_weighted"]["accuracy"],
                        "bottom_detection_rate": segments["detection_rate"]["BOTTOM"],
                        "bottom_onset_p50_ms": segments["onset_latency_ms"]["BOTTOM"]["p50"],
                    }
                ],
                [
                    "n_segments",
                    "macro_f1",
                    "bottom_recall",
                    "segment_uncertain_ratio",
                    "time_accuracy",
                    "bottom_detection_rate",
                    "bottom_onset_p50_ms",
                ],
            ),
        ]
    return "\n".join(lines) + "\n"


def write_report(
    report: Mapping[str, Any],
    out_dir: Path,
    run_name: str,
    *,
    dump_frames: bool = False,
) -> Dict[str, Path]:
    """Write ``gaze_eval.json`` / ``gaze_eval.md`` (and optionally the frames)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {key: value for key, value in report.items() if key != "frames"}
    json_path = out_dir / "gaze_eval.json"
    json_path.write_text(
        json.dumps(jsonable(payload), indent=2, sort_keys=False), encoding="utf-8"
    )
    md_path = out_dir / "gaze_eval.md"
    md_path.write_text(render_markdown(report, run_name), encoding="utf-8")
    written = {"json": json_path, "markdown": md_path}
    if dump_frames and isinstance(report.get("frames"), pd.DataFrame):
        written["frames"] = save_table(report["frames"], out_dir / "scored_frames.parquet")
    return written


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _select_backbone(df: pd.DataFrame, requested: Optional[str]) -> pd.DataFrame:
    """Refuse to score a table that mixes backbones (mirrors doc 23 Experiment 2).

    ``--features-dir`` globs a whole directory, and ``extract_features --backbone a
    --backbone b`` -- the documented way to prepare Experiment 1 -- writes one
    table per backbone into it.  Concatenating those is not a bigger dataset, it
    is the same frames twice: ``sample_id`` repeats, each participant's doc 5-3
    classifier gets fitted on both models' angles at once, and the doc 7 gate
    reads a p95 latency that belongs to neither backbone.  Every number in the
    report would be a blend with no owner, so this is an error rather than a
    warning -- the same call ``exp2_calibration_ablation.resolve_backbone``
    makes, for the same reason.
    """
    if "backbone" not in df.columns:
        if requested:
            raise SystemExit(
                "--backbone was given but the feature table has no 'backbone' column"
            )
        return df

    present = sorted({str(name) for name in df["backbone"].dropna().unique() if str(name)})

    if requested:
        key = str(requested).strip().lower()
        if key not in present:
            raise SystemExit(
                f"backbone {key!r} has no rows; the table holds: {', '.join(present) or '(none)'}"
            )
        return df[df["backbone"].astype(str).str.lower() == key]

    if len(present) > 1:
        raise SystemExit(
            f"the feature table mixes {len(present)} backbones ({', '.join(present)}). "
            "Scoring them together fits every per-user classifier on both models' angles "
            "and reports a latency that belongs to neither. Pass --backbone <name> to pick "
            "one, or use ai/evaluation/experiments/exp1_backbone.py to compare them."
        )
    return df


def load_tables(paths: Sequence[Path]) -> pd.DataFrame:
    """Concatenate feature tables, tagging nothing: the rows carry their own ids."""
    frames = [load_table(path) for path in paths]
    if not frames:
        raise ValueError("no feature table given")
    return pd.concat(frames, ignore_index=True)


def resolve_tables(table_args: Sequence[str], features_dir: Optional[str]) -> List[Path]:
    paths: List[Path] = [Path(arg) for arg in table_args]
    if features_dir:
        directory = Path(features_dir)
        for pattern in ("*.parquet", "*.csv.gz", "*.csv"):
            paths.extend(sorted(directory.glob(pattern)))
    unique: List[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    if not unique:
        raise SystemExit("no feature table found; pass --table or --features-dir")
    return unique


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gaze_eval",
        description=(
            "Score a gaze feature table under the doc 4-3 per-user protocol and write "
            "ai/reports/<run>/gaze_eval.{json,md} (doc 7, doc 19)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--table", action="append", default=[], help="feature table; repeatable"
    )
    parser.add_argument("--features-dir", default=None, help="directory of feature tables")
    parser.add_argument("--backbone", default=None,
                        help="score only this backbone's rows; required when the table holds "
                             "more than one (use exp1_backbone.py to compare backbones)")
    parser.add_argument("--config-dir", default=None, help="override ai/configs")
    parser.add_argument("--run-name", default=None, help="report folder name")
    parser.add_argument("--out-dir", default=None, help="override ai/reports/<run>")
    parser.add_argument(
        "--split",
        choices=("all", "train", "val", "test"),
        default="all",
        help="evaluate one participant split (doc 4-3)",
    )
    parser.add_argument("--split-seed", type=int, default=42)
    parser.add_argument(
        "--split-ratios",
        default="0.6,0.2,0.2",
        help="train,val,test participant ratios",
    )
    parser.add_argument(
        "--segments",
        action="append",
        default=[],
        help="segment label file for the doc 7 segment metrics; repeatable",
    )
    parser.add_argument(
        "--no-temporal", action="store_true", help="skip the doc 6 smoother"
    )
    parser.add_argument(
        "--use-stored-predictions",
        action="store_true",
        help="score the table's pred_label instead of refitting per user",
    )
    parser.add_argument(
        "--drop-failed-calibration",
        action="store_true",
        help="exclude RETRY_REQUIRED participants from the metrics (still counted)",
    )
    parser.add_argument("--p-max", type=float, default=None, help="override p_max_threshold")
    parser.add_argument("--margin", type=float, default=None, help="override margin_threshold")
    parser.add_argument(
        "--dump-frames", action="store_true", help="also write the scored frames table"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(Path(args.config_dir) if args.config_dir else None)
    if args.p_max is not None:
        cfg.calibration.p_max_threshold = float(args.p_max)
    if args.margin is not None:
        cfg.calibration.margin_threshold = float(args.margin)

    paths = resolve_tables(args.table, args.features_dir)
    df = load_tables(paths)
    df = _select_backbone(df, args.backbone)

    if args.split != "all":
        ratios = tuple(float(part) for part in args.split_ratios.split(","))
        splits = participant_split(df["participant_id"].unique(), ratios, args.split_seed)
        df = df[df["participant_id"].isin(splits[args.split])]
        if df.empty:
            raise SystemExit(f"split {args.split!r} is empty for these participants")

    segments = []
    for path in args.segments:
        segments.extend(load_segments(Path(path)))

    report = run_evaluation(
        df,
        cfg,
        smooth=not args.no_temporal,
        use_stored_predictions=args.use_stored_predictions,
        drop_failed_calibration=args.drop_failed_calibration,
        segments=segments or None,
    )

    run_name = args.run_name or datetime.now(timezone.utc).strftime("gaze_eval_%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else REPORTS_DIR / run_name
    report["run"] = {
        "name": run_name,
        "tables": [str(path) for path in paths],
        "split": args.split,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    written = write_report(report, out_dir, run_name, dump_frames=args.dump_frames)

    gate = report["release_gate"]
    print(f"scored {report['dataset']['n_rows_scored']} frames from {len(paths)} table(s)")
    print(
        f"macro_f1={report['frame']['macro_f1']:.4f} "
        f"bottom_recall={report['frame']['bottom_recall']:.4f} "
        f"uncertain_ratio={report['frame']['uncertain_ratio']:.4f}"
    )
    print(f"release gate: {'PASSED' if gate['passed'] else 'FAILED'}")
    for name, path in written.items():
        print(f"  {name}: {path}")
    # Non-zero exit on a failed gate so CI can branch on it; the report is
    # written either way.
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
