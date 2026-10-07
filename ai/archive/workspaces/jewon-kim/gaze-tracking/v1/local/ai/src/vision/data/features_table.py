"""The offline evaluation substrate: one row of *numbers* per analysed frame.

doc 20 forbids keeping raw video around for evaluation, so every experiment
(doc 23) and every failure bucket (doc 19) runs off this table instead of
pixels.  A row therefore has to carry everything an evaluator could want:
identity, ground truth, recording metadata, the backbone output, head pose,
validity, the cheap quality signals and the latency breakdown.

Storage: parquet when an engine is installed, gzipped CSV otherwise.  The
fallback is transparent -- :func:`save_table` rewrites the suffix and returns
the path it actually wrote, and :func:`load_table` accepts either -- because
neither ``pyarrow`` nor ``fastparquet`` is guaranteed on the machines this rig
runs on, and an evaluation must not fail on a serialisation detail.  Both
backends round-trip bit-exactly, but the CSV one only because :func:`load_table`
pins the reader: ``float_precision="round_trip"`` for the numbers and an
explicit ``dtype`` map for the identity columns.  Drop either and the fallback
stops agreeing with parquet -- floats in the last ULP, and a zero-padded id like
``07`` read back as the integer ``7``, which no longer joins the doc 17
manifest.
"""

from __future__ import annotations

import functools
import os
import warnings
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

import pandas as pd

from vision.schemas import (
    FrameObservation,
    GazeDecision,
    GazeLabel,
    GazeVector,
    ParticipantMeta,
    SegmentLabel,
)

from vision.data.manifest import make_sample_id

PathLike = Union[str, Path]

#: Column order of the feature table.  Stable: doc 18 reproduces an experiment
#: from a stored table, and a reordered CSV header would silently break a
#: positional reader.  New columns go at the END of the list.
FEATURE_COLUMNS: List[str] = [
    # identity -- joins back to the doc 17 manifest
    "sample_id",
    "participant_id",
    "session_id",
    "frame_id",
    "t_ms",
    # ground truth (doc 4-2)
    "label",
    "label_version",
    "label_source",
    "condition",
    # recording metadata; these are the doc 19 bucket keys
    "glasses",
    "lighting",
    "device_group",
    "camera_position",
    # backbone output (doc 3-2), radians, schemas.py sign convention
    "backbone",
    "gaze_yaw",
    "gaze_pitch",
    "gaze_confidence",
    # head pose (doc 3-1), radians
    "head_yaw",
    "head_pitch",
    "head_roll",
    "head_reprojection_error",
    "head_depth_proxy",
    # frame validity (doc 3-1 invalid-frame rules)
    "face_valid",
    "face_confidence",
    "invalid_reason",
    # cheap quality signals, mirrored from FrameQuality
    "q_face_area_ratio",
    "q_face_brightness",
    "q_background_brightness",
    "q_face_contrast",
    "q_left_eye_openness",
    "q_right_eye_openness",
    "q_landmark_visibility",
    "q_touches_border",
    "q_backlight_ratio",
    # per-frame decision (doc 5-4); empty when the table predates calibration
    "pred_label",
    "p_camera",
    "p_bottom",
    "uncertain_reason",
    # timing, for the doc 7 latency gate
    "preprocess_ms",
    "inference_ms",
    "latency_ms",
]

_FEATURE_COLUMN_SET = frozenset(FEATURE_COLUMNS)

_BOOL_COLUMNS = ("glasses", "face_valid", "q_touches_border")
_INT_COLUMNS = ("frame_id", "t_ms")
_STR_COLUMNS = (
    "sample_id",
    "participant_id",
    "session_id",
    "label",
    "label_version",
    "label_source",
    "condition",
    "lighting",
    "device_group",
    "camera_position",
    "backbone",
    "invalid_reason",
    "pred_label",
    "uncertain_reason",
)

#: Spelled out, NOT derived as "everything the other three lists did not
#: claim".  The derived version made an unclassified column a float by default,
#: and ``_coerce_dtypes`` then ran ``to_numeric(errors="coerce")`` over it and
#: NaN-ed every value away -- a new text column would have arrived at the
#: evaluation empty, with nothing raised anywhere.  Adding a column here is the
#: price of adding one to :data:`FEATURE_COLUMNS`.
_FLOAT_COLUMNS = (
    "gaze_yaw",
    "gaze_pitch",
    "gaze_confidence",
    "head_yaw",
    "head_pitch",
    "head_roll",
    "head_reprojection_error",
    "head_depth_proxy",
    "face_confidence",
    "q_face_area_ratio",
    "q_face_brightness",
    "q_background_brightness",
    "q_face_contrast",
    "q_left_eye_openness",
    "q_right_eye_openness",
    "q_landmark_visibility",
    "q_backlight_ratio",
    "p_camera",
    "p_bottom",
    "preprocess_ms",
    "inference_ms",
    "latency_ms",
)


def _assert_dtype_partition() -> None:
    """Fail at import unless the four dtype groups partition FEATURE_COLUMNS.

    Loud on purpose, and at import rather than at first use: the failure mode
    this replaces was silent (see :data:`_FLOAT_COLUMNS`), and a column that
    nobody has classified must not be discoverable only as a table full of NaN
    three hours into an evaluation run.  ``raise``, not ``assert``, so ``-O``
    cannot switch the guard off.
    """
    classified = list(_BOOL_COLUMNS) + list(_INT_COLUMNS) + list(_STR_COLUMNS) + list(_FLOAT_COLUMNS)
    duplicated = sorted({column for column in classified if classified.count(column) > 1})
    unclassified = [column for column in FEATURE_COLUMNS if column not in set(classified)]
    unknown = sorted(set(classified) - _FEATURE_COLUMN_SET)
    if duplicated or unclassified or unknown:
        raise RuntimeError(
            "the dtype groups must exactly partition FEATURE_COLUMNS "
            f"(unclassified={unclassified}, in two groups={duplicated}, not a feature column={unknown}); "
            "classify the column in _BOOL_COLUMNS, _INT_COLUMNS, _STR_COLUMNS or _FLOAT_COLUMNS"
        )


_assert_dtype_partition()

#: dtypes pinned for the CSV reader.  Only the string group needs it: an
#: identity column is text to every consumer, but an inferring reader turns the
#: participant id "07" into the integer 7 and the doc 17 join key stops
#: matching.  The numeric and boolean groups are re-typed by
#: :func:`_coerce_dtypes` after the read, so pinning them here would buy
#: nothing.
_CSV_READ_DTYPES: Dict[str, Any] = {column: str for column in _STR_COLUMNS}

_TRUE_TOKENS = frozenset({"true", "t", "yes", "y", "1"})
_FALSE_TOKENS = frozenset({"false", "f", "no", "n", "0"})


# --------------------------------------------------------------------------
# Row construction
# --------------------------------------------------------------------------


def observations_to_rows(
    obs: FrameObservation,
    gaze: Optional[GazeVector],
    decision: Optional[GazeDecision],
    meta: ParticipantMeta,
    session_id: str,
    label: Union[str, SegmentLabel, None] = None,
    *,
    condition: Optional[str] = None,
    lighting: Optional[str] = None,
    backbone: Optional[str] = None,
) -> Dict[str, Any]:
    """Flatten one analysed frame into exactly ``FEATURE_COLUMNS``.

    ``label`` is either the ground-truth string or the ``SegmentLabel`` the
    frame fell into; passing the segment is preferred because ``condition``,
    ``lighting``, ``label_version`` and ``source`` then come from the label file
    instead of being guessed here.  ``gaze`` and ``decision`` are ``None`` on an
    invalid frame or before a classifier exists -- their columns stay empty
    rather than being filled with a neutral-looking 0.5.

    Missing values are ``None``, never a sentinel number: doc 19 buckets filter
    on these columns and a fabricated 0.0 would land a frame in the wrong
    bucket.
    """
    record = obs.to_record()

    if isinstance(label, SegmentLabel):
        label_value = label.label
        label_version = label.label_version
        label_source = label.source
        condition_value = condition if condition is not None else label.condition
        lighting_value = lighting if lighting is not None else label.lighting
        glasses = bool(label.glasses or meta.glasses)
        device_group = label.device_group or meta.device_group
    else:
        label_value = GazeLabel.coerce(label).value if label is not None else None
        label_version = "gaze_label_v1"
        label_source = "protocol"
        condition_value = condition if condition is not None else "unknown"
        lighting_value = lighting if lighting is not None else "normal"
        glasses = bool(meta.glasses)
        device_group = meta.device_group

    row: Dict[str, Any] = {
        "sample_id": make_sample_id(meta.participant_id, session_id, obs.frame_id),
        "participant_id": meta.participant_id,
        "session_id": str(session_id),
        "frame_id": int(obs.frame_id),
        "t_ms": int(obs.t_ms),
        "label": label_value,
        "label_version": label_version,
        "label_source": label_source,
        "condition": condition_value,
        "glasses": glasses,
        "lighting": lighting_value,
        "device_group": device_group,
        "camera_position": meta.camera_position,
        "backbone": (gaze.backbone if gaze is not None else backbone),
        "gaze_yaw": (float(gaze.gaze_yaw) if gaze is not None else None),
        "gaze_pitch": (float(gaze.gaze_pitch) if gaze is not None else None),
        "gaze_confidence": (float(gaze.confidence) if gaze is not None else None),
        "head_yaw": float(record["head_yaw"]),
        "head_pitch": float(record["head_pitch"]),
        "head_roll": float(record["head_roll"]),
        "head_reprojection_error": float(record["head_reprojection_error"]),
        "head_depth_proxy": float(record["head_depth_proxy"]),
        "face_valid": bool(record["face_valid"]),
        "face_confidence": float(record["face_confidence"]),
        "invalid_reason": record["invalid_reason"],
        "q_face_area_ratio": float(record["q_face_area_ratio"]),
        "q_face_brightness": float(record["q_face_brightness"]),
        "q_background_brightness": float(record["q_background_brightness"]),
        "q_face_contrast": float(record["q_face_contrast"]),
        "q_left_eye_openness": float(record["q_left_eye_openness"]),
        "q_right_eye_openness": float(record["q_right_eye_openness"]),
        "q_landmark_visibility": float(record["q_landmark_visibility"]),
        "q_touches_border": bool(record["q_touches_border"]),
        "q_backlight_ratio": float(record["q_backlight_ratio"]),
        "pred_label": (decision.label if decision is not None else None),
        "p_camera": (float(decision.p_camera) if decision is not None else None),
        "p_bottom": (float(decision.p_bottom) if decision is not None else None),
        "uncertain_reason": (decision.uncertain_reason if decision is not None else None),
        "preprocess_ms": float(record["preprocess_ms"]),
        "inference_ms": (float(gaze.inference_ms) if gaze is not None else None),
        "latency_ms": (float(decision.latency_ms) if decision is not None else None),
    }

    extra = sorted(set(row) - _FEATURE_COLUMN_SET)
    missing = [column for column in FEATURE_COLUMNS if column not in row]
    if extra or missing:
        raise RuntimeError(
            f"row does not match FEATURE_COLUMNS (extra={extra}, missing={missing}); "
            "update FEATURE_COLUMNS and observations_to_rows together"
        )
    return {column: row[column] for column in FEATURE_COLUMNS}


def rows_to_frame(rows: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    """Build a typed DataFrame with the canonical column order.

    An empty run still yields the full set of columns, so downstream code can
    select a column without guarding for the zero-frame case.
    """
    frame = pd.DataFrame(list(rows), columns=FEATURE_COLUMNS)
    return _coerce_dtypes(frame)


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


#: Set to 1/true to force the CSV path even where an engine is installed.
#: Read at first call (the answer is cached), which is what lets a test cover
#: the fallback without uninstalling anything.
FORCE_CSV_ENV = "GAZE_TRACKING_FORCE_CSV"


@functools.lru_cache(maxsize=1)
def parquet_available() -> bool:
    """True when pandas can actually write parquet on this machine.

    Checked by import rather than by a trial write, and cached, because the
    answer decides file *names*: ``extract_features`` builds the output path
    before it has a row to write.  Call ``parquet_available.cache_clear()``
    after installing an engine inside a live process.
    """
    if os.environ.get(FORCE_CSV_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return False
    for engine in ("pyarrow", "fastparquet"):
        try:
            __import__(engine)
            return True
        except ImportError:
            continue
    return False


def table_suffix() -> str:
    """The suffix :func:`save_table` will use for a bare stem."""
    return ".parquet" if parquet_available() else ".csv.gz"


def resolve_table_path(path: PathLike) -> Path:
    """Path :func:`save_table` would write for ``path`` on this machine."""
    target = Path(path)
    name = target.name.lower()
    if name.endswith(".csv") or name.endswith(".csv.gz"):
        return target
    if name.endswith(".parquet") or name.endswith(".pq"):
        return target if parquet_available() else target.with_name(target.stem + ".csv.gz")
    return target.with_name(target.name + table_suffix())


def save_table(df: pd.DataFrame, path: PathLike) -> Path:
    """Write the feature table, falling back from parquet to gzipped CSV.

    Returns the path actually written -- it differs from ``path`` when a
    ``.parquet`` name was requested without an engine installed.  Columns are
    reordered to :data:`FEATURE_COLUMNS`; extra columns (doc 19 bucket flags,
    for instance) are kept and appended in their existing order rather than
    dropped.
    """
    target = resolve_table_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    known = [column for column in FEATURE_COLUMNS if column in df.columns]
    extra = [column for column in df.columns if column not in _FEATURE_COLUMN_SET]
    ordered = df[known + extra]

    if target.name.lower().endswith((".parquet", ".pq")):
        try:
            ordered.to_parquet(target, index=False)
            return target
        except Exception as exc:  # a broken engine must not lose an evaluation run
            target.unlink(missing_ok=True)
            fallback = target.with_name(target.stem + ".csv.gz")
            warnings.warn(
                f"parquet write failed ({type(exc).__name__}: {exc}); wrote {fallback.name} instead",
                RuntimeWarning,
                stacklevel=2,
            )
            target = fallback
    ordered.to_csv(target, index=False, compression="infer")
    return target


def load_table(path: PathLike) -> pd.DataFrame:
    """Read a feature table written by :func:`save_table`.

    Accepts the parquet name even when the file on disk is the CSV fallback (and
    the reverse), so a config or report that recorded one name still resolves.
    dtypes are normalised on the way out: gzipped CSV loses ``None`` and
    booleans, and evaluation code must not have to care which backend wrote the
    file.  The CSV reader settings that make the two backends agree are pinned
    below -- read the comment there before changing any of them.
    """
    target = Path(path)
    candidates = [target]
    stem = target.name
    for suffix in (".parquet", ".pq", ".csv.gz", ".csv"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    for suffix in (".parquet", ".pq", ".csv.gz", ".csv"):
        candidate = target.with_name(stem + suffix)
        if candidate not in candidates:
            candidates.append(candidate)

    found = next((candidate for candidate in candidates if candidate.exists()), None)
    if found is None:
        tried = ", ".join(str(candidate) for candidate in candidates)
        raise FileNotFoundError(f"no feature table found; tried: {tried}")

    if found.name.lower().endswith((".parquet", ".pq")):
        frame = pd.read_parquet(found)
    else:
        # Every keyword here is load-bearing; none of them is a default worth
        # falling back to.
        #   dtype       -- the identity columns are text.  Left to inference,
        #                  pandas read the participant id "07" back as 7 and the
        #                  join to the manifest quietly matched nothing.
        #   keep_default_na / na_values
        #               -- with dtype pinned, only the empty field means
        #                  "missing"; the default NA list would also swallow a
        #                  condition or backbone literally named "NA" or "None".
        #                  Empty still becomes NA here and None in _coerce_dtypes.
        #   float_precision
        #               -- round_trip parsing, not the default fast one: pandas'
        #                  default float reader is off by up to 1 ULP, which
        #                  would make a re-run of a stored experiment (doc 18)
        #                  disagree with the original in the last digits.
        # Keys naming a column the file does not have are ignored by the reader,
        # so a partial table (some canonical columns absent) still loads.
        frame = pd.read_csv(
            found,
            compression="infer",
            dtype=_CSV_READ_DTYPES,
            keep_default_na=False,
            na_values=[""],
            float_precision="round_trip",
        )
    return _coerce_dtypes(frame)


# --------------------------------------------------------------------------
# dtype normalisation
# --------------------------------------------------------------------------


def _bool_scalar(value: Any) -> Optional[bool]:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
        if token == "":
            return None
        raise ValueError(f"cannot read {value!r} as a boolean")
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return bool(value)


def _str_scalar(value: Any) -> Optional[str]:
    """Normalise every flavour of "missing" a backend can hand back to ``None``."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and value != value:  # NaN from a CSV blank
        return None
    text = str(value)
    return text if text else None


def _coerce_dtypes(frame: pd.DataFrame) -> pd.DataFrame:
    """Give every known column the dtype the evaluation code expects.

    Only columns declared in :data:`FEATURE_COLUMNS` are touched; anything a
    later stage added is left exactly as it was found.
    """
    out = frame.copy()
    for column in _INT_COLUMNS:
        if column in out.columns:
            numeric = pd.to_numeric(out[column], errors="coerce")
            out[column] = numeric.astype("int64") if not numeric.isna().any() else numeric.astype("Int64")
    for column in _FLOAT_COLUMNS:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce").astype("float64")
    for column in _BOOL_COLUMNS:
        if column in out.columns:
            mapped = out[column].map(_bool_scalar)
            out[column] = mapped.astype("boolean") if mapped.isna().any() else mapped.astype(bool)
    for column in _STR_COLUMNS:
        if column in out.columns:
            series = out[column]
            # Built as an explicit object Series: pandas >= 3 would otherwise infer
            # the ``str`` dtype and turn every missing value back into NaN, so
            # "no invalid_reason" would stop comparing equal to None.
            out[column] = pd.Series(
                [_str_scalar(value) for value in series.tolist()],
                index=series.index,
                dtype=object,
            )
    return out
