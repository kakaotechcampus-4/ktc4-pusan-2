"""doc 19 failure buckets, derived from the feature table only.

doc 19 names ten situations the gaze model is expected to struggle with.  Each
one has to become a boolean over ``vision.data.features_table.FEATURE_COLUMNS``,
because doc 20 keeps numbers rather than video and the buckets have to be
re-derivable from a stored table months later.  Where the doc's phrase names a
*cause* the table cannot see, the rule below encodes the closest observable
*symptom* and says so; those approximations are listed here rather than hidden
in the report:

* **glasses reflection** -> ``glasses``.  The table has the collector's glasses
  flag (doc 4-1 metadata); reflection itself is a pixel property and no column
  carries it.  The bucket is therefore "wears glasses", which is a superset.
* **partial occlusion** -> landmarks leaving the frame or a bbox on the border.
  A hand in front of the mouth still produces 478 in-bounds landmarks, so real
  occlusion is not separable from this table; what the rule catches is the
  *self-occlusion / out-of-frame* half of the bucket.
* **leaning back** -> the participant's own distance baseline.  ``head_depth_proxy``
  is centimetres over focal pixels (``preprocess/headpose.py``), so it changes
  with capture resolution and cannot carry an absolute threshold; the rule uses
  the ratio to that participant's own median instead, which is resolution-free.

Two buckets are defined against the *ground-truth* label ("head down but eyes on
camera", "eyes down but head straight").  That is deliberate: the bucket
describes a situation, and the situation is only defined relative to where the
person was actually looking.  A consequence to read the report with: those
buckets contain one class only, so the other class's recall is 0 by
construction and only the named class's recall is meaningful.

Thresholds live in :class:`BucketThresholds` so a report can state the numbers
it used, and so a future dataset can move one without editing a predicate.
"""

from __future__ import annotations

import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

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

from vision.schemas import DECISION_CLASSES, GazeLabel  # noqa: E402

from evaluation.metrics import frame_metrics  # noqa: E402

#: Prefix of the boolean columns :func:`assign_buckets` adds.  Prefixed because
#: ``glasses`` is already a feature-table column and a bucket must never
#: overwrite the metadata it was derived from.
BUCKET_COLUMN_PREFIX = "bucket_"

#: Prefix of the helper columns :func:`assign_buckets` derives from row
#: *context* (neighbouring frames, per-participant baselines).  A predicate that
#: needs one of these cannot be evaluated on an isolated row, which is why
#: :func:`assign_buckets` is the only supported entry point.
CONTEXT_COLUMN_PREFIX = "ctx_"


@dataclass(frozen=True)
class BucketThresholds:
    """Cut points for the doc 19 rules, with the reasoning for each default."""

    #: ``FrameQuality.backlight_ratio`` doc comment: "> ~1.6 indicates strong
    #: backlight" (schemas.py).
    backlight_ratio: float = 1.6
    #: 3x the ``min_face_area_ratio`` that makes a frame invalid (0.010,
    #: preprocess.yaml): small enough to hurt, large enough to still be scored.
    small_face_area_ratio: float = 0.030
    #: 15 % further from the camera than this participant's own median distance.
    lean_back_depth_ratio: float = 1.15
    #: ~11.5 deg of chin-down; below the head-pose noise floor nothing is "down".
    head_down_pitch_rad: float = -0.20
    #: ~5.7 deg: the head is straight enough that only the eyes moved.
    head_straight_pitch_rad: float = 0.10
    #: doc 6's slowest dwell is 600 ms, so a label change within 1 s is a
    #: transition the smoother is still resolving.
    transition_window_ms: float = 1000.0
    #: Any landmark outside the image at all; the in-bounds fraction is 1.0 on a
    #: clean frame (``preprocess/landmarker.in_bounds_fraction``).
    landmark_visibility: float = 0.995
    #: Mean face luminance out of 255.  Below ~60 a webcam is already gaining up
    #: and the iris contrast the geometric backbone needs is going.
    low_light_face_brightness: float = 60.0
    #: ``lighting`` metadata values (doc 4-1) that mean the room was dark.
    low_light_metadata: Tuple[str, ...] = ("dim", "dark", "low", "low_light", "backlit_dim")
    #: ``camera_position`` values that are NOT the doc 19 "webcam bottom/side" case.
    centered_camera_positions: Tuple[str, ...] = ("top_center", "top", "center", "unknown", "")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_THRESHOLDS = BucketThresholds()


# --------------------------------------------------------------------------
# Row accessors
# --------------------------------------------------------------------------


def _num(row: Mapping[str, Any], column: str) -> float:
    """Numeric cell as float, or NaN when absent/missing.

    Missing reads as NaN and every comparison against NaN is False, so a table
    written before a column existed puts its rows in no bucket instead of
    raising in the middle of a report.
    """
    value = row.get(column, None)
    if value is None or value is pd.NA:
        return math.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _flag(row: Mapping[str, Any], column: str) -> bool:
    value = row.get(column, None)
    if value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        return False
    return bool(value)


def _text(row: Mapping[str, Any], column: str) -> str:
    value = row.get(column, None)
    if value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip().lower()


def _label(row: Mapping[str, Any]) -> str:
    return _text(row, "label").upper()


# --------------------------------------------------------------------------
# The doc 19 rules
# --------------------------------------------------------------------------


def _make_buckets(t: BucketThresholds) -> Dict[str, Callable[[pd.Series], bool]]:
    return {
        "glasses": lambda row: _flag(row, "glasses"),
        "backlight": lambda row: _num(row, "q_backlight_ratio") >= t.backlight_ratio,
        # ``> 0`` excludes frames with no bbox at all: NO_FACE is a different
        # failure from a face that was found and is far away.
        "small_face": lambda row: 0.0 < _num(row, "q_face_area_ratio") < t.small_face_area_ratio,
        "leaning_back": lambda row: _num(row, "ctx_depth_ratio") >= t.lean_back_depth_ratio,
        "head_down_eyes_camera": lambda row: (
            _num(row, "head_pitch") <= t.head_down_pitch_rad
            and _label(row) == GazeLabel.CAMERA.value
        ),
        "eyes_down_head_straight": lambda row: (
            abs(_num(row, "head_pitch")) <= t.head_straight_pitch_rad
            and _label(row) == GazeLabel.BOTTOM.value
        ),
        "fast_transition": lambda row: _num(row, "ctx_ms_to_label_change") <= t.transition_window_ms,
        "partial_occlusion": lambda row: (
            _num(row, "q_landmark_visibility") < t.landmark_visibility
            or _flag(row, "q_touches_border")
        ),
        "camera_off_center": lambda row: (
            _text(row, "camera_position") not in t.centered_camera_positions
        ),
        "low_light": lambda row: (
            _num(row, "q_face_brightness") < t.low_light_face_brightness
            or _text(row, "lighting") in t.low_light_metadata
        ),
    }


#: doc 19 bucket -> row predicate.  Built from :data:`DEFAULT_THRESHOLDS`;
#: :func:`assign_buckets` rebuilds the same dict when given other thresholds.
BUCKETS: Dict[str, Callable[[pd.Series], bool]] = _make_buckets(DEFAULT_THRESHOLDS)

#: What each bucket means and exactly how it is derived, for the report and for
#: the reader who has doc 19 open next to it.
BUCKET_RULES: Dict[str, Tuple[str, str]] = {
    "glasses": (
        "glasses reflection",
        "glasses == True (collector metadata; reflection itself is not in the table)",
    ),
    "backlight": ("strong backlight", "q_backlight_ratio >= backlight_ratio"),
    "small_face": ("small face", "0 < q_face_area_ratio < small_face_area_ratio"),
    "leaning_back": (
        "leaning back",
        "head_depth_proxy >= lean_back_depth_ratio x this participant's median depth",
    ),
    "head_down_eyes_camera": (
        "head down but eyes on camera",
        "head_pitch <= head_down_pitch_rad and label == CAMERA",
    ),
    "eyes_down_head_straight": (
        "eyes down but head straight",
        "abs(head_pitch) <= head_straight_pitch_rad and label == BOTTOM",
    ),
    "fast_transition": (
        "fast CAMERA<->BOTTOM transition",
        "within transition_window_ms of a ground-truth CAMERA<->BOTTOM change",
    ),
    "partial_occlusion": (
        "partial occlusion",
        "q_landmark_visibility < landmark_visibility or q_touches_border "
        "(out-of-frame only; true occlusion is not observable here)",
    ),
    "camera_off_center": (
        "webcam at bottom / side",
        "camera_position not in centered_camera_positions",
    ),
    "low_light": (
        "low light",
        "q_face_brightness < low_light_face_brightness or lighting in low_light_metadata",
    ),
}


def bucket_rules_table(thresholds: Optional[BucketThresholds] = None) -> pd.DataFrame:
    """The rule book as a frame: bucket, doc 19 phrase, derivation."""
    _ = thresholds or DEFAULT_THRESHOLDS
    return pd.DataFrame(
        [
            {"bucket": name, "doc19": phrase, "rule": rule}
            for name, (phrase, rule) in BUCKET_RULES.items()
        ]
    )


# --------------------------------------------------------------------------
# Context columns
# --------------------------------------------------------------------------


def _ms_to_label_change(group: pd.DataFrame, label_col: str) -> pd.Series:
    """Distance in ms from each frame to the nearest CAMERA<->BOTTOM change.

    Change points are found on the sequence with ``IGNORE`` removed, so the doc
    4-2 guard band does not read as two separate changes, and are placed at the
    midpoint between the last frame of one class and the first of the next --
    the cue change happened somewhere in that gap and the table cannot say
    where.  Guard-band frames themselves therefore sit close to a change, which
    is correct: they are the transition.
    """
    ordered = group.sort_values("t_ms", kind="stable")
    decided = ordered[ordered[label_col].isin(list(DECISION_CLASSES))]
    times = decided["t_ms"].to_numpy(dtype=float)
    labels = decided[label_col].to_numpy(dtype=object)
    changes = [
        0.5 * (times[i] + times[i + 1])
        for i in range(len(times) - 1)
        if labels[i] != labels[i + 1]
    ]
    if not changes:
        return pd.Series(math.inf, index=group.index, dtype=float)
    change_times = np.asarray(changes, dtype=float)
    own = group["t_ms"].to_numpy(dtype=float)
    distance = np.abs(own[:, None] - change_times[None, :]).min(axis=1)
    return pd.Series(distance, index=group.index, dtype=float)


def add_context_columns(df: pd.DataFrame, *, label_col: str = "label") -> pd.DataFrame:
    """Add the ``ctx_`` columns the neighbourhood-dependent buckets need.

    ``ctx_ms_to_label_change`` is computed per ``(participant_id, session_id)``
    because time only means anything inside one recording, and
    ``ctx_depth_ratio`` per participant because it is a within-person baseline.

    The index is made unique for the duration: a table concatenated from several
    takes carries duplicate labels, and a group-wise result cannot be reindexed
    back onto those.  The caller's index is restored before returning.
    """
    out = df.copy()
    saved_index = out.index
    out = out.reset_index(drop=True)
    if out.empty:
        # Still declare the columns: a caller that selects one must not have to
        # branch on "did this run have any frames".
        for column in ("ctx_ms_to_label_change", "ctx_depth_ratio"):
            out[column] = pd.Series(dtype=float)
        out.index = saved_index
        return out

    if "t_ms" in out.columns and label_col in out.columns:
        keys = [key for key in ("participant_id", "session_id") if key in out.columns]
        if keys:
            pieces = [
                _ms_to_label_change(group, label_col)
                for _, group in out.groupby(keys, sort=False, dropna=False)
            ]
            out["ctx_ms_to_label_change"] = pd.concat(pieces).reindex(out.index)
        else:
            out["ctx_ms_to_label_change"] = _ms_to_label_change(out, label_col)

    if "head_depth_proxy" in out.columns and "participant_id" in out.columns:
        depth = pd.to_numeric(out["head_depth_proxy"], errors="coerce")
        valid = depth.where(depth > 0)
        if "face_valid" in out.columns:
            valid = valid.where(out["face_valid"].astype("boolean").fillna(False))
        baseline = valid.groupby(out["participant_id"], dropna=False).transform("median")
        out["ctx_depth_ratio"] = (depth / baseline).where(baseline > 0)
    out.index = saved_index
    return out


# --------------------------------------------------------------------------
# Assignment and reporting
# --------------------------------------------------------------------------


def assign_buckets(
    df: pd.DataFrame,
    thresholds: Optional[BucketThresholds] = None,
    *,
    label_col: str = "label",
) -> pd.DataFrame:
    """Add one ``bucket_<name>`` boolean column per doc 19 bucket.

    The context columns are derived first, so every predicate sees a complete
    row; the returned frame keeps them (they are the evidence behind two of the
    buckets and are worth having in a saved report).  Buckets are not mutually
    exclusive -- a small backlit face in glasses is in three of them -- which is
    the point: doc 19 asks where the model breaks, not how to partition frames.
    """
    thresholds = thresholds or DEFAULT_THRESHOLDS
    predicates = _make_buckets(thresholds)
    out = add_context_columns(df, label_col=label_col)
    if out.empty:
        for name in predicates:
            out[BUCKET_COLUMN_PREFIX + name] = pd.Series(dtype=bool)
        return out
    records = out.to_dict(orient="records")
    for name, predicate in predicates.items():
        out[BUCKET_COLUMN_PREFIX + name] = [bool(predicate(row)) for row in records]
    return out


def bucket_columns(df: pd.DataFrame) -> List[str]:
    """Bucket columns present in ``df``, in :data:`BUCKETS` order."""
    return [
        BUCKET_COLUMN_PREFIX + name
        for name in BUCKETS
        if BUCKET_COLUMN_PREFIX + name in df.columns
    ]


def bucket_report(
    df: pd.DataFrame,
    thresholds: Optional[BucketThresholds] = None,
    *,
    label_col: str = "label",
    pred_col: str = "pred_label",
) -> pd.DataFrame:
    """n / macro F1 / BOTTOM recall / UNCERTAIN ratio per doc 19 bucket.

    ``df`` may be raw or already bucketed; missing bucket columns are derived
    here.  The first row is ``ALL`` and every other row carries
    ``macro_f1_delta`` against it -- a bucket is interesting when it is *worse
    than the run*, and an absolute F1 alone does not say that.

    Empty buckets are kept with ``n = 0`` and NaN metrics rather than dropped:
    "this dataset never exercised the backlight bucket" is a finding about the
    dataset, and a silently missing row reads as a pass.
    """
    frame = df if bucket_columns(df) else assign_buckets(df, thresholds, label_col=label_col)
    if pred_col not in frame.columns:
        raise ValueError(f"bucket_report needs a prediction column {pred_col!r}")

    def _row(name: str, subset: pd.DataFrame) -> Dict[str, Any]:
        scores = frame_metrics(subset[label_col], subset[pred_col]) if len(subset) else None
        return {
            "bucket": name,
            "n": int(len(subset)),
            "n_labelled": scores["n_labelled"] if scores else 0,
            "n_decided": scores["n_decided"] if scores else 0,
            "uncertain_ratio": scores["uncertain_ratio"] if scores else math.nan,
            "macro_f1": scores["macro_f1"] if scores else math.nan,
            "bottom_recall": scores["bottom_recall"] if scores else math.nan,
            "camera_recall": scores["camera_recall"] if scores else math.nan,
            "accuracy": scores["accuracy"] if scores else math.nan,
            "n_bottom": scores["per_class"]["BOTTOM"]["support"] if scores else 0,
            "n_camera": scores["per_class"]["CAMERA"]["support"] if scores else 0,
        }

    rows = [_row("ALL", frame)]
    for name in BUCKETS:
        column = BUCKET_COLUMN_PREFIX + name
        if column not in frame.columns:
            continue
        mask = frame[column].astype("boolean").fillna(False).to_numpy(dtype=bool)
        rows.append(_row(name, frame[mask]))

    report = pd.DataFrame(rows)
    overall = report.loc[0, "macro_f1"]
    report["macro_f1_delta"] = report["macro_f1"] - overall
    report.loc[0, "macro_f1_delta"] = 0.0
    return report


def bucket_coverage(df: pd.DataFrame) -> Dict[str, int]:
    """Frame count per bucket -- the cheap "did we record this case at all?" check."""
    frame = df if bucket_columns(df) else assign_buckets(df)
    return {
        column[len(BUCKET_COLUMN_PREFIX) :]: int(
            frame[column].astype("boolean").fillna(False).sum()
        )
        for column in bucket_columns(frame)
    }
