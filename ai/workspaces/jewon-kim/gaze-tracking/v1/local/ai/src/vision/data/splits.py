"""Dataset splitting (doc 4-3).

Two rules from doc 4-3, and both exist because breaking them inflates every
number in the report:

1. **Split people, not frames.**  Consecutive frames at 8 fps are near
   duplicates; a random frame split puts the same face, lighting and glasses on
   both sides and measures memorisation.  :func:`participant_split` therefore
   only ever partitions participant ids.
2. **Per user, calibrate on the first block and evaluate on what follows.**
   The classifier is per-user (doc 5-3), so within a participant the
   calibration frames are training data.  They must come from the *first*
   calibration block, and the evaluation must start after that block ends --
   not merely after the last calibration frame, since the remainder of the same
   static block is the same pose held still.
"""

from __future__ import annotations

import math
import random
import warnings
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from vision.config import CalibrationConfig, VisionConfig
from vision.schemas import GazeLabel

#: Split names, in allocation order.  Fixed so a stored split (doc 18) reads
#: back the same way.
SPLIT_NAMES: Tuple[str, str, str] = ("train", "val", "test")

#: A ``condition`` whose name starts with this is an explicit calibration block
#: written by the collector; when present it beats the label-run heuristic.
_CALIBRATION_CONDITION_PREFIX = "calib"

_REQUIRED_COLUMNS = ("participant_id", "session_id", "t_ms", "label")


# --------------------------------------------------------------------------
# Participant-wise splitting
# --------------------------------------------------------------------------


def participant_split(
    participant_ids: Iterable[str],
    ratios: Sequence[float] = (0.6, 0.2, 0.2),
    seed: int = 42,
) -> Dict[str, List[str]]:
    """Partition *participants* into train/val/test (doc 4-3).

    Deterministic for a given ``(set of ids, ratios, seed)``: ids are
    de-duplicated and sorted before the shuffle, so the split does not depend on
    the order a directory listing happened to return.

    Sizes use largest-remainder allocation, then a repair pass moves one
    participant into any split that a rounding step left empty while another
    split holds more than one.  With fewer participants than splits the
    lowest-ratio splits stay empty -- reported, not silently filled with an id
    that also lives in ``train``.
    """
    ids = sorted({str(pid) for pid in participant_ids})
    if len(ratios) != len(SPLIT_NAMES):
        raise ValueError(f"expected {len(SPLIT_NAMES)} ratios {SPLIT_NAMES}, got {len(ratios)}")
    if any(r < 0 for r in ratios):
        raise ValueError(f"ratios must be >= 0, got {tuple(ratios)}")
    total_ratio = float(sum(ratios))
    if total_ratio <= 0:
        raise ValueError("ratios must not all be zero")
    weights = [float(r) / total_ratio for r in ratios]

    shuffled = list(ids)
    random.Random(seed).shuffle(shuffled)

    n = len(shuffled)
    quotas = [n * w for w in weights]
    counts = [int(math.floor(q)) for q in quotas]
    remainder = n - sum(counts)
    # Largest fractional part first; index breaks ties so the result is stable.
    order = sorted(range(len(counts)), key=lambda i: (-(quotas[i] - counts[i]), i))
    for i in order[:remainder]:
        counts[i] += 1

    for i, weight in enumerate(weights):
        if weight <= 0 or counts[i] > 0:
            continue
        donor = max(
            (j for j in range(len(counts)) if j != i and counts[j] > 1),
            key=lambda j: (counts[j], -j),
            default=None,
        )
        if donor is None:
            break  # not enough participants to fill every split
        counts[donor] -= 1
        counts[i] += 1

    splits: Dict[str, List[str]] = {}
    cursor = 0
    for name, count in zip(SPLIT_NAMES, counts):
        splits[name] = sorted(shuffled[cursor : cursor + count])
        cursor += count

    _assert_disjoint(splits, ids)
    return splits


def _assert_disjoint(splits: Dict[str, List[str]], ids: Sequence[str]) -> None:
    """doc 4-3's hard invariant: a participant belongs to exactly one split."""
    seen: Dict[str, str] = {}
    for name, members in splits.items():
        for pid in members:
            if pid in seen:
                raise RuntimeError(f"participant {pid} is in both {seen[pid]} and {name}")
            seen[pid] = name
    if set(seen) != set(ids):
        missing = sorted(set(ids) - set(seen))
        raise RuntimeError(f"participants dropped by the split: {missing}")


def split_frame(df: pd.DataFrame, splits: Dict[str, List[str]], name: str) -> pd.DataFrame:
    """Rows of ``df`` belonging to one split, in table order."""
    if name not in splits:
        raise KeyError(f"unknown split {name!r}; have {sorted(splits)}")
    _require_columns(df, ("participant_id",))
    return df[df["participant_id"].isin(set(splits[name]))].copy()


# --------------------------------------------------------------------------
# Per-participant calibration / evaluation partition
# --------------------------------------------------------------------------


def _require_columns(df: pd.DataFrame, columns: Sequence[str]) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise ValueError(f"feature table is missing required column(s): {missing}")


def _calibration_config(cfg: Any) -> CalibrationConfig:
    """Accept a whole ``VisionConfig``, its calibration section, or ``None``."""
    if cfg is None:
        return CalibrationConfig()
    if isinstance(cfg, VisionConfig):
        return cfg.calibration
    if isinstance(cfg, CalibrationConfig):
        return cfg
    section = getattr(cfg, "calibration", None)
    if isinstance(section, CalibrationConfig):
        return section
    raise TypeError(f"expected VisionConfig or CalibrationConfig, got {type(cfg).__name__}")


def _participant_first_session(df: pd.DataFrame, participant_id: str) -> pd.DataFrame:
    """Rows of the participant's earliest session, sorted in recording order."""
    _require_columns(df, _REQUIRED_COLUMNS)
    sub = df[df["participant_id"] == participant_id]
    if sub.empty:
        return sub
    starts = sub.groupby("session_id")["t_ms"].min().sort_values(kind="stable")
    first_session = starts.index[0]
    sort_keys = ["t_ms", "frame_id"] if "frame_id" in sub.columns else ["t_ms"]
    return sub[sub["session_id"] == first_session].sort_values(sort_keys, kind="stable")


def _first_run_window(session: pd.DataFrame, label: str, seconds: float, not_before_ms: Optional[int]) -> pd.DataFrame:
    """Frames carrying ``label`` in the first ``seconds`` after it first appears.

    Not "the first run", whatever the name suggests: the mask is on the label
    alone, so a frame that carries ``label`` again later inside the window is
    selected too, even though a differently-labelled frame sits between them.
    That is deliberate.  doc 4-1's static blocks are contiguous by protocol, so
    a hole in the middle of one is preprocessing dropout, not a new block, and
    cutting the selection at the hole would shorten the calibration set for
    exactly the participants whose preprocessing is already struggling.

    ``not_before_ms`` pushes the start past a block already taken (the CAMERA
    window, when picking BOTTOM).  If no frame with this label exists at or
    after it the constraint is *dropped* rather than returning nothing, because
    doc 4-1 does not fix the order of the two static blocks and a take recorded
    BOTTOM-first must still calibrate.

    The window is half-open: ``[start, start + seconds)``, ``start`` being the
    ``t_ms`` of the first selected frame, not the start of the session.
    """
    mask = session["label"] == label
    if not_before_ms is not None:
        after = mask & (session["t_ms"] >= not_before_ms)
        if bool(after.any()):
            mask = after
    if not bool(mask.any()):
        return session.iloc[0:0]
    start = int(session.loc[mask, "t_ms"].iloc[0])
    window = int(round(float(seconds) * 1000.0))
    return session[mask & (session["t_ms"] < start + window)]


def calibration_frames(df: pd.DataFrame, participant_id: str, cfg: Any = None) -> pd.DataFrame:
    """The participant's calibration frames: the FIRST block only (doc 4-3).

    Selection, in order of preference:

    * rows of the earliest session whose ``condition`` starts with ``calib`` --
      an explicit calibration block written by the collector;
    * otherwise the first ``camera_seconds`` of the earliest CAMERA run and the
      first ``bottom_seconds`` of the earliest BOTTOM run at or after it (doc
      4-1 opens with the two static blocks, but the order is not assumed).

    ``IGNORE`` frames can never be selected -- they carry no ground truth.
    Invalid frames are kept: dropping them here would hide a participant whose
    calibration failed for a preprocessing reason, and doc 5-2 wants to see that
    as a calibration failure rather than as missing data.
    """
    calib = _calibration_config(cfg)
    session = _participant_first_session(df, participant_id)
    if session.empty:
        return session.copy()

    if "condition" in session.columns:
        explicit = session["condition"].astype("string").str.lower().str.startswith(
            _CALIBRATION_CONDITION_PREFIX, na=False
        )
        if bool(explicit.any()):
            session = session[explicit]

    camera = _first_run_window(session, GazeLabel.CAMERA.value, calib.camera_seconds, None)
    camera_end = int(camera["t_ms"].iloc[-1]) if not camera.empty else None
    bottom = _first_run_window(session, GazeLabel.BOTTOM.value, calib.bottom_seconds, camera_end)

    selected = pd.concat([camera, bottom]) if not (camera.empty and bottom.empty) else session.iloc[0:0]
    sort_keys = ["t_ms", "frame_id"] if "frame_id" in selected.columns else ["t_ms"]
    return selected.sort_values(sort_keys, kind="stable").copy()


def calibration_boundary_ms(df: pd.DataFrame, participant_id: str, cfg: Any = None) -> Optional[Tuple[Any, int]]:
    """``(session_id, last calibration-block t_ms)`` for one participant.

    The boundary is the end of the recording *blocks* the calibration frames
    came from, not the last calibration frame: the rest of a 20 s static block
    is the same held pose, so evaluating on it would measure the calibration
    frames again.  Blocks are contiguous runs of ``condition`` inside the
    session.  With no usable ``condition`` column the boundary falls back to the
    last calibration frame, which is the weaker guarantee this table can
    support; that case is worth fixing in the collector, not here.

    ``None`` when the participant has no calibration frames at all.
    """
    calibration = calibration_frames(df, participant_id, cfg)
    if calibration.empty:
        return None

    session_id = calibration["session_id"].iloc[0]
    session = _participant_first_session(df, participant_id)
    session = session[session["session_id"] == session_id]

    boundary = int(calibration["t_ms"].max())
    if "condition" in session.columns and session["condition"].nunique(dropna=False) > 1:
        condition = session["condition"]
        run_id = (condition != condition.shift()).cumsum()
        # Match on t_ms, not on index labels: a table concatenated from several
        # takes can carry duplicate index values.
        selected_runs = run_id[session["t_ms"].isin(set(calibration["t_ms"].tolist()))]
        if not selected_runs.empty:
            boundary = int(session.loc[run_id.isin(set(selected_runs.tolist())), "t_ms"].max())
    return session_id, boundary


def evaluation_frames(
    df: pd.DataFrame,
    participant_id: str,
    cfg: Any = None,
    *,
    drop_ignore: bool = True,
) -> pd.DataFrame:
    """Everything for this participant after the calibration block (doc 4-3).

    That is: rows of the calibration session recorded strictly after the
    calibration block ends, plus every row of any later session.  ``cfg`` must
    be the same config passed to :func:`calibration_frames`, otherwise the two
    windows are computed from different durations and can overlap.

    ``drop_ignore`` removes the doc 4-2 guard-band frames, which have no ground
    truth and must not reach a metric.
    """
    _require_columns(df, _REQUIRED_COLUMNS)
    sub = df[df["participant_id"] == participant_id]
    if sub.empty:
        return sub.copy()

    boundary = calibration_boundary_ms(df, participant_id, cfg)
    if boundary is None:
        selected = sub
    else:
        session_id, boundary_ms = boundary
        in_calibration_session = sub["session_id"] == session_id
        selected = sub[(~in_calibration_session) | (sub["t_ms"] > boundary_ms)]

    if drop_ignore:
        selected = selected[selected["label"] != GazeLabel.IGNORE.value]

    sort_keys = [key for key in ("session_id", "t_ms", "frame_id") if key in selected.columns]
    return selected.sort_values(sort_keys, kind="stable").copy()


def _frame_keys(df: pd.DataFrame) -> set:
    """Identity of each row for leakage checking: ``sample_id`` when present."""
    if "sample_id" in df.columns:
        return set(df["sample_id"].tolist())
    return set(zip(df["session_id"].tolist(), df["t_ms"].tolist()))


def per_participant_partition(
    df: pd.DataFrame,
    cfg: Any = None,
    *,
    drop_ignore: bool = True,
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """Run the doc 4-3 per-user protocol over every participant in the table.

    Raises when a participant's calibration and evaluation frames intersect --
    the leakage this module exists to prevent, checked on real ``sample_id``
    values rather than trusted from the interval arithmetic above.

    Without a ``sample_id`` column that check is not the same check: it falls
    back to ``(session_id, t_ms)`` identity, which cannot see a frame duplicated
    at its own timestamp, so it can only report that the *intervals* do not
    overlap -- which is what it was supposed to be verifying independently.  It
    still runs (an old table must stay loadable), but it says so now: silence
    used to be indistinguishable from a real pass.
    """
    _require_columns(df, _REQUIRED_COLUMNS)
    if "sample_id" not in df.columns:
        warnings.warn(
            "no sample_id column: the doc 4-3 leakage check degrades to "
            "(session_id, t_ms) identity and cannot see a frame duplicated at its own "
            "timestamp; it re-reads the interval arithmetic instead of checking it",
            RuntimeWarning,
            stacklevel=2,
        )
    out: Dict[str, Dict[str, pd.DataFrame]] = {}
    for participant_id in sorted(df["participant_id"].astype(str).unique()):
        calibration = calibration_frames(df, participant_id, cfg)
        evaluation = evaluation_frames(df, participant_id, cfg, drop_ignore=drop_ignore)
        overlap = _frame_keys(calibration) & _frame_keys(evaluation)
        if overlap:
            raise RuntimeError(
                f"{participant_id}: {len(overlap)} frame(s) are in both calibration and "
                "evaluation (doc 4-3 leakage)"
            )
        out[participant_id] = {"calibration": calibration, "evaluation": evaluation}
    return out
