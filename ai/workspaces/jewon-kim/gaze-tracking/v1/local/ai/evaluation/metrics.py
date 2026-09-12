"""Metrics for the CAMERA / BOTTOM gaze task (doc 7).

The one rule this module exists to enforce: **UNCERTAIN is not a wrong answer,
and it is not a free pass either.**  doc 5-4 lets the classifier abstain, and
doc 7 gates the abstention rate separately (``max_uncertain_ratio``), so the
accuracy numbers here are computed over *decided* frames and the abstentions
are reported next to them as ``uncertain_ratio`` / ``coverage``.  Every
returned dict therefore carries three views of the same run:

``macro_f1`` / ``bottom_recall``
    over decided frames only -- what the release gate reads, because the
    abstention rate has its own gate row and counting it twice would make the
    two thresholds interact.
``macro_f1_uncertain_as_error`` / ``bottom_recall_uncertain_as_error``
    the pessimistic reading, where an abstention on a labelled frame is a miss
    for that class (precision is untouched: an abstention is never *claimed* as
    a class).  A large gap between the two views means the model is buying its
    F1 with abstentions.
``confusion``
    a 2x3 table with UNCERTAIN as its own predicted column, so nothing is
    hidden by aggregation.

Ground truth of ``IGNORE`` (doc 4-2 guard bands) and rows with no ground truth
are excluded from every number and counted in ``n_ignored``; a missing
*prediction* is folded into UNCERTAIN, since "the pipeline produced nothing"
and "the pipeline declined" are the same thing to a consumer.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd


def _bootstrap_import_path() -> None:
    """Make ``vision.*`` importable when this file is reached as a script.

    ``ai/evaluation`` is not under ``ai/src``, so ``python ai/evaluation/...``
    and ``PYTHONPATH=ai`` both start without the package root on the path.
    Idempotent, and a no-op under pytest (which sets ``pythonpath = ai/src``).
    """
    root = Path(__file__).resolve().parents[2]
    for entry in (root / "ai" / "src", root / "ai"):
        text = str(entry)
        if text not in sys.path:
            sys.path.insert(0, text)


_bootstrap_import_path()

from vision.config import ReleaseGateConfig  # noqa: E402
from vision.schemas import (  # noqa: E402
    DECISION_CLASSES,
    GazeLabel,
    GazeState,
    GazeStateEvent,
    SegmentLabel,
)

#: Predicted-class order used by every confusion matrix and per-class table.
CLASSES: Tuple[str, str] = DECISION_CLASSES
UNCERTAIN: str = GazeState.UNCERTAIN.value
IGNORE: str = GazeLabel.IGNORE.value

#: Accepted spellings of "no decision" on the prediction side.
_UNDECIDED = frozenset({UNCERTAIN, "", "NONE", "NAN", "NULL"})

EventLike = Union[GazeStateEvent, Mapping[str, Any], Sequence[Any]]
SegmentLike = Union[SegmentLabel, Mapping[str, Any]]


# --------------------------------------------------------------------------
# Frame-level
# --------------------------------------------------------------------------


def _clean_label(value: Any) -> Optional[str]:
    """Upper-case label string, or ``None`` for every flavour of missing."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if value is pd.NA or value is pd.NaT:
        return None
    text = str(getattr(value, "value", value)).strip().upper()
    return text or None


def _split_truth_prediction(
    y_true: Iterable[Any], y_pred: Iterable[Any]
) -> Tuple[List[str], List[str], int]:
    """Aligned (truth, prediction) over scorable rows, plus the excluded count.

    Raises on an unknown class rather than dropping it: a typo in a label file
    that silently shrinks the denominator is the exact failure this evaluation
    is supposed to catch.
    """
    truths = [_clean_label(v) for v in y_true]
    predictions = [_clean_label(v) for v in y_pred]
    if len(truths) != len(predictions):
        raise ValueError(f"y_true has {len(truths)} rows, y_pred has {len(predictions)}")

    kept_true: List[str] = []
    kept_pred: List[str] = []
    ignored = 0
    for index, (truth, prediction) in enumerate(zip(truths, predictions)):
        if truth is None or truth == IGNORE:
            ignored += 1
            continue
        if truth not in CLASSES:
            raise ValueError(f"row {index}: unknown ground-truth label {truth!r}")
        if prediction is not None and prediction not in CLASSES and prediction not in _UNDECIDED:
            raise ValueError(f"row {index}: unknown predicted label {prediction!r}")
        kept_true.append(truth)
        kept_pred.append(prediction if prediction in CLASSES else UNCERTAIN)
    return kept_true, kept_pred, ignored


def _prf(tp: int, fp: int, fn: int) -> Dict[str, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * tp) / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0
    return {"precision": float(precision), "recall": float(recall), "f1": float(f1)}


def frame_metrics(
    y_true: Iterable[Any],
    y_pred: Iterable[Any],
    *,
    uncertain_reasons: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    """Per-frame metrics with the doc 7 UNCERTAIN accounting made explicit.

    ``uncertain_reasons`` is optional and aligned with the other two sequences;
    when given, the abstentions are broken down by
    ``GazeDecision.uncertain_reason`` so doc 19 can tell a lost face from a
    low-margin call.

    All three inputs are materialised once, up front, because the reason
    breakdown reads the truth/prediction rows a *second* time and the signature
    promises ``Iterable``.  Re-reading the arguments meant a one-shot iterable
    (a generator, ``zip(...)``, ``iter(list)``) arrived empty at that second
    pass and tripped the length check, so a caller got "uncertain_reasons must
    be the same length as y_true / y_pred" for sequences that were the same
    length -- and only when ``uncertain_reasons`` was supplied.  ``list`` copies
    the rows; it does not consume the caller's own sequence.

    With nothing decided at all -- an empty input, or a participant the model
    abstained on for every frame -- every rate is ``0.0`` rather than NaN.  That
    is deliberate: NaN would drop out of the doc 7 per-user *minimum* and let a
    participant who was never classified pass the gate, while
    ``uncertain_ratio`` of 1.0 right next to it says what actually happened.
    """
    truth_values = list(y_true)
    pred_values = list(y_pred)
    truths, predictions, ignored = _split_truth_prediction(truth_values, pred_values)

    counts: Dict[str, Dict[str, int]] = {
        truth: {prediction: 0 for prediction in (*CLASSES, UNCERTAIN)} for truth in CLASSES
    }
    for truth, prediction in zip(truths, predictions):
        counts[truth][prediction] += 1

    n_labelled = len(truths)
    n_uncertain = sum(counts[truth][UNCERTAIN] for truth in CLASSES)
    n_decided = n_labelled - n_uncertain

    per_class: Dict[str, Dict[str, float]] = {}
    per_class_pessimistic: Dict[str, Dict[str, float]] = {}
    for cls in CLASSES:
        other = [c for c in CLASSES if c != cls]
        tp = counts[cls][cls]
        fp = sum(counts[o][cls] for o in other)
        fn = sum(counts[cls][o] for o in other)
        abstained = counts[cls][UNCERTAIN]
        stats = _prf(tp, fp, fn)
        stats["support"] = float(tp + fn + abstained)
        stats["support_decided"] = float(tp + fn)
        stats["uncertain"] = float(abstained)
        per_class[cls] = stats
        # Abstentions count as false negatives only; nothing was predicted as
        # this class, so precision cannot be affected by them.
        per_class_pessimistic[cls] = _prf(tp, fp, fn + abstained)

    correct = sum(counts[cls][cls] for cls in CLASSES)
    macro_f1 = float(np.mean([per_class[cls]["f1"] for cls in CLASSES]))
    macro_f1_pessimistic = float(np.mean([per_class_pessimistic[cls]["f1"] for cls in CLASSES]))

    out: Dict[str, Any] = {
        "n": n_labelled + ignored,
        "n_labelled": n_labelled,
        "n_ignored": ignored,
        "n_decided": n_decided,
        "n_uncertain": n_uncertain,
        "uncertain_ratio": (n_uncertain / n_labelled) if n_labelled else 0.0,
        "coverage": (n_decided / n_labelled) if n_labelled else 0.0,
        "accuracy": (correct / n_decided) if n_decided else 0.0,
        "macro_f1": macro_f1,
        "bottom_recall": per_class[GazeLabel.BOTTOM.value]["recall"],
        "camera_recall": per_class[GazeLabel.CAMERA.value]["recall"],
        "per_class": per_class,
        "macro_f1_uncertain_as_error": macro_f1_pessimistic,
        "bottom_recall_uncertain_as_error": per_class_pessimistic[GazeLabel.BOTTOM.value]["recall"],
        "per_class_uncertain_as_error": per_class_pessimistic,
        "confusion": counts,
        "confusion_matrix": [[counts[t][p] for p in CLASSES] for t in CLASSES],
        "confusion_labels": list(CLASSES),
    }
    if uncertain_reasons is not None:
        out["uncertain_by_reason"] = _uncertain_reason_counts(
            truth_values, pred_values, list(uncertain_reasons)
        )
    return out


def _uncertain_reason_counts(
    y_true: Iterable[Any], y_pred: Iterable[Any], reasons: Sequence[Any]
) -> Dict[str, int]:
    """Abstentions on labelled frames, grouped by ``uncertain_reason``."""
    truths = [_clean_label(v) for v in y_true]
    predictions = [_clean_label(v) for v in y_pred]
    if not (len(truths) == len(predictions) == len(reasons)):
        raise ValueError("uncertain_reasons must be the same length as y_true / y_pred")
    tally: Dict[str, int] = {}
    for truth, prediction, reason in zip(truths, predictions, reasons):
        if truth not in CLASSES or prediction in CLASSES:
            continue
        key = _clean_label(reason) or "UNSPECIFIED"
        tally[key] = tally.get(key, 0) + 1
    return dict(sorted(tally.items(), key=lambda item: (-item[1], item[0])))


# --------------------------------------------------------------------------
# Per participant
# --------------------------------------------------------------------------


def per_user_metrics(
    df: pd.DataFrame,
    *,
    label_col: str = "label",
    pred_col: str = "pred_label",
    group_col: str = "participant_id",
    latency_col: str = "latency_ms",
) -> pd.DataFrame:
    """One metrics row per participant (doc 7 "per-user F1 min/mean/std").

    The min/mean/std aggregates live in ``result.attrs["summary"]`` rather than
    in extra rows, so the frame stays one-row-per-participant and can be joined
    or sorted without dropping a fake participant first; :func:`per_user_summary`
    recomputes the same dict from any such frame.

    doc 7 gates on the per-user *minimum* because the pipeline is per-user (doc
    5-3): one bad calibration destroys one participant completely while the
    pooled number barely moves.
    """
    for column in (label_col, pred_col, group_col):
        if column not in df.columns:
            raise ValueError(f"feature table is missing required column {column!r}")

    rows: List[Dict[str, Any]] = []
    for participant_id, group in df.groupby(group_col, sort=True, dropna=False):
        scores = frame_metrics(group[label_col], group[pred_col])
        row: Dict[str, Any] = {
            group_col: participant_id,
            "n": scores["n"],
            "n_labelled": scores["n_labelled"],
            "n_decided": scores["n_decided"],
            "n_uncertain": scores["n_uncertain"],
            "uncertain_ratio": scores["uncertain_ratio"],
            "accuracy": scores["accuracy"],
            "macro_f1": scores["macro_f1"],
            "camera_f1": scores["per_class"][GazeLabel.CAMERA.value]["f1"],
            "bottom_f1": scores["per_class"][GazeLabel.BOTTOM.value]["f1"],
            "camera_recall": scores["camera_recall"],
            "bottom_recall": scores["bottom_recall"],
            "macro_f1_uncertain_as_error": scores["macro_f1_uncertain_as_error"],
        }
        if latency_col in group.columns:
            row["latency_p95_ms"] = latency_stats(group[latency_col])["p95"]
        rows.append(row)

    result = pd.DataFrame(rows)
    result.attrs["summary"] = per_user_summary(result)
    return result


def per_user_summary(per_user: pd.DataFrame) -> Dict[str, Any]:
    """min / mean / std of the per-user table (doc 7).

    ``std`` is the sample standard deviation and is ``0.0`` for a single
    participant -- reported as zero spread rather than as NaN, because a
    one-person run has no spread to measure and a NaN here would poison an
    otherwise fine report.
    """
    out: Dict[str, Any] = {"n_participants": int(len(per_user))}
    for column in ("macro_f1", "bottom_recall", "uncertain_ratio", "latency_p95_ms"):
        if column not in per_user.columns:
            continue
        values = pd.to_numeric(per_user[column], errors="coerce").dropna().to_numpy(dtype=float)
        if values.size == 0:
            out.update({f"{column}_{stat}": None for stat in ("mean", "min", "max", "std")})
            continue
        out[f"{column}_mean"] = float(values.mean())
        out[f"{column}_min"] = float(values.min())
        out[f"{column}_max"] = float(values.max())
        out[f"{column}_std"] = float(values.std(ddof=1)) if values.size > 1 else 0.0
    return out


# --------------------------------------------------------------------------
# Segment-level (doc 7: scored on real presentation segments)
# --------------------------------------------------------------------------


def _event_pairs(events: Iterable[EventLike]) -> List[Tuple[int, str]]:
    """``(t_ms, label)`` pairs from events, state tuples or plain dicts."""
    pairs: List[Tuple[int, str]] = []
    for event in events:
        if isinstance(event, GazeStateEvent):
            t_ms, label = event.t_ms, event.label
        elif isinstance(event, Mapping):
            t_ms, label = event["t_ms"], event.get("label")
        else:
            sequence = list(event)
            if len(sequence) < 2:
                raise ValueError(f"cannot read a (t_ms, label) pair from {event!r}")
            t_ms, label = sequence[0], sequence[1]
        pairs.append((int(t_ms), _clean_label(label) or UNCERTAIN))
    pairs.sort(key=lambda pair: pair[0])
    return pairs


def _segment_bounds(segment: SegmentLike) -> Tuple[int, int, str]:
    if isinstance(segment, SegmentLabel):
        return int(segment.start_ms), int(segment.end_ms), segment.label
    return int(segment["start_ms"]), int(segment["end_ms"]), str(segment["label"])


def _segment_participant(segment: SegmentLike) -> Optional[str]:
    if isinstance(segment, SegmentLabel):
        return segment.participant_id
    return segment.get("participant_id")


def _hold_ms(pairs: Sequence[Tuple[int, str]], hold_last_ms: Optional[float]) -> float:
    """How long the final event stays valid.

    Defaults to the median inter-event gap: the last state has to cover its own
    frame, but extrapolating it to the end of a segment would credit or blame
    the model for time it never observed.
    """
    if hold_last_ms is not None:
        return float(hold_last_ms)
    if len(pairs) < 2:
        return 0.0
    gaps = np.diff([pair[0] for pair in pairs])
    return float(np.median(gaps)) if gaps.size else 0.0


def segment_predictions(
    events_or_states: Iterable[EventLike],
    segments: Iterable[SegmentLike],
    *,
    hold_last_ms: Optional[float] = None,
    require_overlap: bool = True,
) -> List[Dict[str, Any]]:
    """One record per scorable segment: covered time per state and the verdict.

    ``events_or_states`` is either stream shape the pipeline can hand over, as
    the parameter name says: the sparse ``GAZE_STATE`` stream (doc 6-2 emits
    transitions and heartbeats, not one event per frame) *or* a dense per-frame
    state stream, which is what ``gaze_eval`` scores on a run with no smoother.
    Both are read the same way, as a piecewise-constant timeline -- a state
    holds until the next one, and the last one for ``hold_last_ms`` (by default
    the median inter-event gap) -- because that is what a consumer of
    ``GAZE_STATE`` actually sees; a dense stream is just that same timeline
    sampled every frame.  An entry may be a ``GazeStateEvent``, a mapping with
    ``t_ms`` / ``label``, or a ``(t_ms, label)`` pair, and the stream is sorted
    by time here rather than assumed sorted.  A segment's prediction is the
    class that covered the most time in it; segments with no decided coverage
    are ``None`` and counted as abstentions, never as errors.

    ``require_overlap`` drops segments that lie entirely outside the stream's
    own time span.  A label file covers a whole recording, including the
    calibration block that doc 4-3 removes from the evaluation set, and scoring
    a segment the harness deliberately never looked at as an abstention would
    invent an UNCERTAIN rate out of the protocol.  Set it False to see every
    segment in the file.
    """
    pairs = _event_pairs(events_or_states)
    hold = _hold_ms(pairs, hold_last_ms)
    intervals: List[Tuple[float, float, str]] = []
    for index, (t_ms, label) in enumerate(pairs):
        end = float(pairs[index + 1][0]) if index + 1 < len(pairs) else float(t_ms) + hold
        if end > t_ms:
            intervals.append((float(t_ms), end, label))
    window = (intervals[0][0], intervals[-1][1]) if intervals else None

    records: List[Dict[str, Any]] = []
    for segment in segments:
        start_ms, end_ms, raw_label = _segment_bounds(segment)
        truth = _clean_label(raw_label)
        if truth not in CLASSES or end_ms <= start_ms:
            continue
        if require_overlap and (
            window is None or end_ms <= window[0] or start_ms >= window[1]
        ):
            continue
        covered: Dict[str, float] = {cls: 0.0 for cls in (*CLASSES, UNCERTAIN)}
        onset_ms: Optional[float] = None
        for interval_start, interval_end, label in intervals:
            overlap = min(float(end_ms), interval_end) - max(float(start_ms), interval_start)
            if overlap <= 0:
                continue
            covered[label if label in CLASSES else UNCERTAIN] += overlap
            if label == truth and onset_ms is None:
                onset_ms = max(float(start_ms), interval_start) - float(start_ms)
        decided = {cls: covered[cls] for cls in CLASSES}
        best = max(decided, key=lambda cls: decided[cls]) if max(decided.values()) > 0 else None
        records.append(
            {
                "participant_id": _segment_participant(segment),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration_ms": end_ms - start_ms,
                "label": truth,
                "pred_label": best,
                "covered_ms": covered,
                "onset_latency_ms": onset_ms,
            }
        )
    return records


def segment_metrics_from_predictions(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Score the records of :func:`segment_predictions`, pooled across streams."""
    truths = [record["label"] for record in records]
    predictions = [record["pred_label"] or UNCERTAIN for record in records]
    scores = frame_metrics(truths, predictions)

    covered: Dict[str, Dict[str, float]] = {
        truth: {prediction: 0.0 for prediction in (*CLASSES, UNCERTAIN)} for truth in CLASSES
    }
    for record in records:
        for prediction, milliseconds in record["covered_ms"].items():
            covered[record["label"]][prediction] += float(milliseconds)

    total_ms = sum(sum(row.values()) for row in covered.values())
    decided_ms = total_ms - sum(row[UNCERTAIN] for row in covered.values())
    correct_ms = sum(covered[cls][cls] for cls in CLASSES)

    onsets: Dict[str, List[float]] = {cls: [] for cls in CLASSES}
    detected: Dict[str, int] = {cls: 0 for cls in CLASSES}
    totals: Dict[str, int] = {cls: 0 for cls in CLASSES}
    for record in records:
        totals[record["label"]] += 1
        if record["onset_latency_ms"] is not None:
            detected[record["label"]] += 1
            onsets[record["label"]].append(float(record["onset_latency_ms"]))

    return {
        "n_segments": len(records),
        "n_segments_decided": scores["n_decided"],
        "segment_uncertain_ratio": scores["uncertain_ratio"],
        "macro_f1": scores["macro_f1"],
        "bottom_recall": scores["bottom_recall"],
        "camera_recall": scores["camera_recall"],
        "accuracy": scores["accuracy"],
        "per_class": scores["per_class"],
        "confusion": scores["confusion"],
        "time_weighted": {
            "total_ms": float(total_ms),
            "decided_ms": float(decided_ms),
            "uncertain_time_ratio": float(1.0 - decided_ms / total_ms) if total_ms else 0.0,
            "accuracy": float(correct_ms / decided_ms) if decided_ms else 0.0,
            "covered_ms": {truth: dict(row) for truth, row in covered.items()},
        },
        "detection_rate": {
            cls: (detected[cls] / totals[cls]) if totals[cls] else None for cls in CLASSES
        },
        "onset_latency_ms": {cls: latency_stats(onsets[cls]) for cls in CLASSES},
    }


def segment_metrics(
    events_or_states: Iterable[EventLike],
    segments: Iterable[SegmentLike],
    *,
    hold_last_ms: Optional[float] = None,
) -> Dict[str, Any]:
    """doc 7 segment-level score for one participant's event stream.

    Segment level answers a different question from frame level: a take is a
    sequence of held glances, and a consumer cares whether each glance was
    reported at all (``detection_rate``), how late (``onset_latency_ms``) and
    for how much of its duration (``time_weighted``) -- not how many 125 ms
    frames were right.
    """
    return segment_metrics_from_predictions(
        segment_predictions(events_or_states, segments, hold_last_ms=hold_last_ms)
    )


# --------------------------------------------------------------------------
# Latency
# --------------------------------------------------------------------------


def latency_stats(values: Iterable[Any]) -> Dict[str, Any]:
    """p50 / p95 / p99 / mean / max over a latency sample (doc 7, doc 3-3).

    Missing and non-finite entries are dropped and counted in ``n_dropped``;
    percentiles use numpy's linear interpolation.  An empty sample yields
    ``None`` for every statistic rather than 0.0, so a run that never measured
    latency cannot accidentally *pass* the gate.
    """
    raw = pd.to_numeric(pd.Series(list(values), dtype="object"), errors="coerce")
    array = raw.to_numpy(dtype=float, na_value=np.nan)
    clean = array[np.isfinite(array)]
    dropped = int(array.size - clean.size)
    if clean.size == 0:
        return {
            "n": 0,
            "n_dropped": dropped,
            "p50": None,
            "p95": None,
            "p99": None,
            "mean": None,
            "min": None,
            "max": None,
        }
    percentiles = np.percentile(clean, [50, 95, 99])
    return {
        "n": int(clean.size),
        "n_dropped": dropped,
        "p50": float(percentiles[0]),
        "p95": float(percentiles[1]),
        "p99": float(percentiles[2]),
        "mean": float(clean.mean()),
        "min": float(clean.min()),
        "max": float(clean.max()),
    }


# --------------------------------------------------------------------------
# Release gate (doc 7)
# --------------------------------------------------------------------------

#: ``(row name, ReleaseGateConfig field, direction, lookup keys)``.  The lookup
#: keys are tried in order and may be dotted paths into nested summary dicts,
#: so both a flat summary and the nested one ``gaze_eval`` writes work.
_GATE_SPEC: Tuple[Tuple[str, str, str, Tuple[str, ...]], ...] = (
    ("macro_f1", "min_macro_f1", "min", ("macro_f1", "frame.macro_f1")),
    ("bottom_recall", "min_bottom_recall", "min", ("bottom_recall", "frame.bottom_recall")),
    ("per_user_f1_min", "min_per_user_f1", "min", ("per_user_f1_min", "per_user.macro_f1_min")),
    ("uncertain_ratio", "max_uncertain_ratio", "max", ("uncertain_ratio", "frame.uncertain_ratio")),
    (
        "calibration_failure_rate",
        "max_calibration_failure_rate",
        "max",
        ("calibration_failure_rate", "calibration.failure_rate"),
    ),
    ("p95_latency_ms", "max_p95_latency_ms", "max", ("p95_latency_ms", "latency.p95")),
)


def _lookup(summary: Mapping[str, Any], keys: Sequence[str]) -> Optional[float]:
    for key in keys:
        node: Any = summary
        for part in key.split("."):
            if not isinstance(node, Mapping) or part not in node:
                node = None
                break
            node = node[part]
        if node is None or isinstance(node, bool):
            continue
        try:
            value = float(node)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return None


def evaluate_release_gate(
    summary: Mapping[str, Any], cfg: Optional[ReleaseGateConfig] = None
) -> Dict[str, Any]:
    """Apply the doc 7 PoC pass line to a run summary.

    A metric that is not in ``summary`` fails its row with ``value=None`` and is
    listed in ``missing``: an unmeasured gate is not a passed gate, and a run
    that never recorded latency must not read as green.
    """
    gate = cfg or ReleaseGateConfig()
    rows: List[Dict[str, Any]] = []
    missing: List[str] = []
    for name, field_name, direction, keys in _GATE_SPEC:
        target = float(getattr(gate, field_name))
        value = _lookup(summary, keys)
        if value is None:
            missing.append(name)
            passed = False
        else:
            passed = value >= target if direction == "min" else value <= target
        rows.append(
            {
                "metric": name,
                "value": value,
                "target": target,
                "direction": direction,
                "pass": bool(passed),
            }
        )
    return {
        "passed": all(row["pass"] for row in rows),
        "rows": rows,
        "missing": missing,
        "config": {
            field_name: float(getattr(gate, field_name)) for _, field_name, _, _ in _GATE_SPEC
        },
    }


def gate_table(gate: Mapping[str, Any]) -> pd.DataFrame:
    """The gate rows as a frame, for markdown rendering."""
    return pd.DataFrame(gate["rows"], columns=["metric", "value", "target", "direction", "pass"])
