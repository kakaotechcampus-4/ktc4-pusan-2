"""Sign-convention sanity checks on a feature table (schemas.py, doc 19).

schemas.py fixes one consequence of its sign convention that the whole pipeline
depends on: **BOTTOM has a more negative ``gaze_pitch`` than CAMERA.**  A
backbone adapter that forgot to convert its native convention still produces a
usable classifier -- a logistic regression happily learns a flipped axis -- so
the mistake survives calibration, survives the release gate, and only shows up
as an inexplicable failure on the next backbone or the next dataset.

The check is per ``(participant_id, backbone)`` and not pooled, because a
pooled mean can stay correctly ordered while one participant is inverted (a
webcam mounted below the screen, doc 19, does exactly that to the head-pose
term).  The verdict is advisory: ``INVERTED`` means "read the adapter", not
"the run is void".
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

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

from vision.schemas import GazeLabel  # noqa: E402

#: Below this many usable frames in either class the means are noise.
MIN_SAMPLES_PER_CLASS = 5

#: Separation in pooled standard deviations under which the ordering is real but
#: too weak to trust as evidence that the adapter is right.
MIN_EFFECT_SIZE = 0.5

VERDICT_OK = "OK"
VERDICT_WEAK = "WEAK"
VERDICT_INVERTED = "INVERTED"
VERDICT_INSUFFICIENT = "INSUFFICIENT"


def _class_values(group: pd.DataFrame, column: str, label: str) -> np.ndarray:
    """Finite values of ``column`` for one ground-truth class."""
    if column not in group.columns:
        return np.zeros(0, dtype=float)
    subset = group[group["label"] == label]
    values = pd.to_numeric(subset[column], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
    return values[np.isfinite(values)]


def _pooled_std(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 2 or b.size < 2:
        return float("nan")
    numerator = (a.size - 1) * np.var(a, ddof=1) + (b.size - 1) * np.var(b, ddof=1)
    return float(np.sqrt(numerator / float(a.size + b.size - 2)))


def pitch_ordering_report(
    df: pd.DataFrame,
    *,
    min_samples: int = MIN_SAMPLES_PER_CLASS,
    min_effect_size: float = MIN_EFFECT_SIZE,
    require_valid: bool = True,
) -> pd.DataFrame:
    """Mean gaze / head pitch per label, per participant and backbone.

    ``delta_gaze_pitch`` is ``mean(CAMERA) - mean(BOTTOM)`` and must be
    **positive** under the schemas.py convention.  ``effect_size`` is that delta
    over the pooled within-class standard deviation, which is what separates
    "correctly ordered" from "correctly ordered by 0.001 rad of noise".

    ``head_pitch`` is reported for context only and carries no verdict: a
    presenter can read a script with their eyes alone (doc 19's "eyes down but
    head straight"), so head pitch is not required to be ordered.

    ``require_valid`` drops ``face_valid == False`` rows, whose angles are
    whatever the failed frame left behind.
    """
    for column in ("label", "gaze_pitch"):
        if column not in df.columns:
            raise ValueError(f"pitch_ordering_report needs column {column!r}")

    frame = df
    if require_valid and "face_valid" in frame.columns:
        frame = frame[frame["face_valid"].astype("boolean").fillna(False).to_numpy(dtype=bool)]

    keys = [key for key in ("participant_id", "backbone") if key in frame.columns]
    groups = frame.groupby(keys, sort=True, dropna=False) if keys else [((), frame)]

    rows: List[Dict[str, Any]] = []
    for key, group in groups:
        camera = _class_values(group, "gaze_pitch", GazeLabel.CAMERA.value)
        bottom = _class_values(group, "gaze_pitch", GazeLabel.BOTTOM.value)
        head_camera = _class_values(group, "head_pitch", GazeLabel.CAMERA.value)
        head_bottom = _class_values(group, "head_pitch", GazeLabel.BOTTOM.value)

        delta = float(camera.mean() - bottom.mean()) if camera.size and bottom.size else float("nan")
        pooled = _pooled_std(camera, bottom)
        effect = float(delta / pooled) if pooled and np.isfinite(pooled) and pooled > 0 else float("nan")

        if min(camera.size, bottom.size) < min_samples:
            verdict = VERDICT_INSUFFICIENT
        elif not np.isfinite(delta) or delta <= 0.0:
            verdict = VERDICT_INVERTED
        elif not np.isfinite(effect) or effect < min_effect_size:
            verdict = VERDICT_WEAK
        else:
            verdict = VERDICT_OK

        row: Dict[str, Any] = dict(zip(keys, key if isinstance(key, tuple) else (key,)))
        row.update(
            {
                "n_camera": int(camera.size),
                "n_bottom": int(bottom.size),
                "mean_gaze_pitch_camera": float(camera.mean()) if camera.size else float("nan"),
                "mean_gaze_pitch_bottom": float(bottom.mean()) if bottom.size else float("nan"),
                "delta_gaze_pitch": delta,
                "pooled_std": pooled,
                "effect_size": effect,
                "mean_head_pitch_camera": float(head_camera.mean())
                if head_camera.size
                else float("nan"),
                "mean_head_pitch_bottom": float(head_bottom.mean())
                if head_bottom.size
                else float("nan"),
                "verdict": verdict,
            }
        )
        rows.append(row)

    report = pd.DataFrame(rows)
    report.attrs["summary"] = pitch_ordering_summary(report)
    return report


def pitch_ordering_summary(report: pd.DataFrame) -> Dict[str, Any]:
    """Roll the per-participant verdicts into one pass/fail plus the offenders.

    ``passed`` is False as soon as one row is INVERTED.  INSUFFICIENT rows do
    not fail the check -- they were never measured -- but they are counted, so a
    run cannot pass by having measured nothing.
    """
    if report.empty:
        # Same key set as the populated path below.  The old early return left
        # ``n_measured`` out, so the one key that says "nothing was checked"
        # was missing on exactly the run where nothing was: a caller reading
        # it got a KeyError instead of the 0 it was asking about.
        return {"passed": False, "n_rows": 0, "n_measured": 0, "verdicts": {}, "inverted": []}
    verdicts = report["verdict"].value_counts().to_dict()
    inverted_rows = report[report["verdict"] == VERDICT_INVERTED]
    keys = [key for key in ("participant_id", "backbone") if key in report.columns]
    inverted = [
        {key: row[key] for key in keys} | {"delta_gaze_pitch": float(row["delta_gaze_pitch"])}
        for _, row in inverted_rows.iterrows()
    ]
    return {
        "passed": len(inverted) == 0,
        "n_rows": int(len(report)),
        "n_measured": int(len(report) - int(verdicts.get(VERDICT_INSUFFICIENT, 0))),
        "verdicts": {str(key): int(value) for key, value in verdicts.items()},
        "inverted": inverted,
    }


def uncertain_reason_report(
    df: pd.DataFrame,
    *,
    reason_col: str = "uncertain_reason",
    invalid_col: str = "invalid_reason",
    pred_col: str = "pred_label",
) -> pd.DataFrame:
    """Why frames were abstained on, counted (doc 5-4 branches, doc 19 triage).

    The report combines the classifier's ``uncertain_reason`` with the
    preprocess ``invalid_reason`` because doc 5-4 copies the latter into the
    former -- a table produced before any classifier ran still has the
    preprocess side, and that is the half that names a doc 19 bucket.
    """
    if pred_col not in df.columns:
        raise ValueError(f"uncertain_reason_report needs column {pred_col!r}")
    predictions = df[pred_col].astype("object")
    undecided = ~predictions.isin(["CAMERA", "BOTTOM"])
    subset = df[undecided.to_numpy(dtype=bool)]

    reasons = pd.Series(["UNSPECIFIED"] * len(subset), index=subset.index, dtype=object)
    for column in (invalid_col, reason_col):
        if column in subset.columns:
            column_values = subset[column].astype("object")
            filled = column_values.notna() & (column_values.astype("string") != "")
            reasons = reasons.where(~filled.to_numpy(dtype=bool), column_values)

    counts = reasons.value_counts(dropna=False)
    total = int(len(df))
    return pd.DataFrame(
        {
            "reason": [str(index) for index in counts.index],
            "n": [int(value) for value in counts.to_numpy()],
            "ratio_of_all_frames": [float(value) / total if total else 0.0 for value in counts.to_numpy()],
        }
    )
