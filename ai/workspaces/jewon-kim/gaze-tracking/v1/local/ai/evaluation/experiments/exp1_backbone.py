"""doc 23 Experiment 1 -- which gaze backbone (doc 3-2), everything else fixed.

The comparison is only worth anything if the backbone is the *only* thing that
moved, so this script holds four things constant across arms and says so in the
report:

* the same participants and the same frames.  ``extract_features --backbone a
  --backbone b`` samples once and runs both models over the identical frames,
  so the arms share ``sample_id`` values; :func:`align_frames` restricts every
  arm to the intersection and reports what it dropped.  Comparing a backbone
  that silently saw 3 % fewer frames is comparing two datasets.
* the same calibration protocol -- doc 4-3's first-block-only partition, via
  ``vision.data.splits.per_participant_partition``.
* the same classifier -- the doc 5-3 logistic regression with the configured
  feature set and thresholds, refitted per user inside each arm because the
  gaze angles it consumes are exactly what the backbone changed.
* **no temporal smoothing.**  doc 6 buys stability with latency and would mask
  a jittery backbone behind the dwell timers; the raw per-frame decision is
  what measures the backbone.  ``--temporal`` turns it on for a second look.

Selection follows doc 3-3, which does not say "pick the best F1": BOTTOM recall
and latency can veto a higher-scoring model.  :func:`select_backbone` therefore
partitions the arms into eligible and vetoed first and ranks only inside the
eligible set, and the report always names the highest-F1 arm next to the
recommendation so a veto is visible rather than implied.  An arm whose veto
rows were never *measured* is not eligible either -- an unmeasured gate is not
a passed gate (``evaluation.metrics.evaluate_release_gate``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


def _bootstrap_import_path() -> None:
    """See ``metrics._bootstrap_import_path``; repeated so any entry point works."""
    root = Path(__file__).resolve().parents[3]
    for entry in (root / "ai" / "src", root / "ai"):
        text = str(entry)
        if text not in sys.path:
            sys.path.insert(0, text)


_bootstrap_import_path()

from vision.config import (  # noqa: E402
    REPORTS_DIR,
    BackboneConfig,
    ReleaseGateConfig,
    VisionConfig,
    load_config,
    resolve_path,
)
from vision.data.features_table import save_table  # noqa: E402
from vision.data.splits import participant_split  # noqa: E402
from vision.runtime.version import MODEL_VERSION, current_ai_version, git_commit  # noqa: E402

from evaluation.experiment_log import (  # noqa: E402
    DEFAULT_LOG_PATH,
    ExperimentRecord,
    dataset_fingerprint,
    make_experiment_id,
    now_utc_iso,
    update_log,
)
from evaluation.gaze_eval import (  # noqa: E402
    jsonable,
    load_tables,
    md_table,
    resolve_tables,
    run_evaluation,
)
from evaluation.metrics import frame_metrics  # noqa: E402

EXPERIMENT = "exp1_backbone"

#: Columns of the doc 23 comparison table, in the order the document asks for
#: them: identity, accuracy, per-user spread, subgroup, cost.
COMPARISON_COLUMNS: Sequence[str] = (
    "backbone",
    "version",
    "n_frames",
    "n_participants",
    "macro_f1",
    "bottom_recall",
    "camera_recall",
    "uncertain_ratio",
    "per_user_f1_mean",
    "per_user_f1_min",
    "per_user_f1_std",
    "glasses_f1",
    "no_glasses_f1",
    "glasses_delta",
    "calibration_failure_rate",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_source",
    "model_size_mb",
    "pitch_ordering",
    "gate_passed",
)


# --------------------------------------------------------------------------
# Backbone facts that do not come from the feature table
# --------------------------------------------------------------------------


def backbone_profile(name: str, cfg: VisionConfig) -> Dict[str, Any]:
    """Version and on-disk weight size for one backbone (doc 23 comparison table).

    The feature table records what a backbone *produced*; the model size and the
    implementation version live with the code, so they are read here.  The
    backbone is instantiated when possible -- that is the only way to get the
    size a specific checkpoint actually has -- and falls back to stat-ing the
    module's ``DEFAULT_CHECKPOINT`` when construction fails, which is the normal
    state of this repo: doc 3-2 forbids a silent fallback, so ``l2cs`` and
    ``gazetr`` raise ``CheckpointMissingError`` on a machine without weights.
    An evaluation that runs off a stored table must not need those weights, so
    the failure is recorded in ``note`` and the arm is still comparable on
    everything else.

    ``cfg.backbone`` is reused verbatim only for the backbone it actually names.
    For the others just the name is taken and ``checkpoint`` is left unset, so
    each falls back to its own default path -- passing one backbone's checkpoint
    to another would load the wrong weights or fail for the wrong reason.
    """
    from vision.backbones.registry import build_backbone, get_backbone_class

    key = str(name).strip().lower()
    profile: Dict[str, Any] = {
        "backbone": key,
        "version": None,
        "model_size_bytes": None,
        "model_size_mb": None,
        "weights": "unavailable",
        "checkpoint": None,
        "note": None,
    }

    def finish() -> Dict[str, Any]:
        size = profile["model_size_bytes"]
        profile["model_size_mb"] = None if size is None else round(size / (1024.0 * 1024.0), 3)
        return profile

    try:
        cls = get_backbone_class(key)
    except ValueError as exc:
        # A table can name a backbone this build does not have (an older run,
        # or a rename); that is worth reporting, not worth aborting.
        profile["note"] = str(exc)
        return finish()
    profile["version"] = getattr(cls, "version", None)

    backbone_cfg = (
        cfg.backbone
        if str(cfg.backbone.name).strip().lower() == key
        else BackboneConfig(
            name=key, device=cfg.backbone.device, num_threads=cfg.backbone.num_threads
        )
    )
    try:
        backbone = build_backbone(backbone_cfg)
    except Exception as exc:  # noqa: BLE001 - reported, never fatal for a table run
        profile["note"] = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
        default_checkpoint = getattr(sys.modules[cls.__module__], "DEFAULT_CHECKPOINT", None)
        if default_checkpoint:
            path = resolve_path(str(default_checkpoint))
            profile["checkpoint"] = str(path)
            if path.is_file():
                profile["model_size_bytes"] = int(path.stat().st_size)
                profile["weights"] = "on_disk"
        return finish()

    try:
        profile["version"] = backbone.version
        profile["model_size_bytes"] = int(backbone.model_size_bytes())
        profile["weights"] = "loaded"
        checkpoint = getattr(backbone, "checkpoint_path", None)
        profile["checkpoint"] = None if checkpoint is None else str(checkpoint)
    finally:
        backbone.close()
    return finish()


# --------------------------------------------------------------------------
# Fairness: the arms must see the same frames
# --------------------------------------------------------------------------


def align_frames(
    df: pd.DataFrame, backbones: Sequence[str]
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Restrict every arm to the frames all of them measured.

    Returns the restricted table and a record of what was dropped.  ``aligned``
    distinguishes the three outcomes the caller has to treat differently:
    ``True`` (the intersection was applied), ``False`` (alignment was attempted
    and the arms share no frame at all -- a fatal setup error), ``None``
    (alignment was not possible because the table carries no ``sample_id``,
    which is a warning, not a lie about the comparison).
    """
    info: Dict[str, Any] = {
        "aligned": None,
        "identical": None,
        "n_common": None,
        "per_backbone": {},
        "note": None,
    }
    if "sample_id" not in df.columns:
        info["note"] = "table has no sample_id column; arms were not aligned"
        return df, info

    per_backbone = {
        name: set(df.loc[df["backbone"] == name, "sample_id"].dropna().tolist())
        for name in backbones
    }
    common = set.intersection(*per_backbone.values()) if per_backbone else set()
    info["n_common"] = len(common)
    info["identical"] = all(ids == common for ids in per_backbone.values())
    info["per_backbone"] = {
        name: {"n": len(ids), "n_dropped": len(ids - common)}
        for name, ids in per_backbone.items()
    }
    if not common:
        info["aligned"] = False
        info["note"] = (
            "the arms share no sample_id; they were extracted from different frames and "
            "cannot be compared frame for frame"
        )
        return df, info

    info["aligned"] = True
    mask = df["backbone"].isin(list(backbones)) & df["sample_id"].isin(common)
    return df[mask].copy(), info


# --------------------------------------------------------------------------
# Subgroups (doc 23: the glasses delta)
# --------------------------------------------------------------------------


def subgroup_delta(
    frames: pd.DataFrame,
    column: str = "glasses",
    *,
    label_col: str = "label",
    pred_col: str = "pred_label",
) -> Dict[str, Any]:
    """macro F1 inside a metadata subgroup minus macro F1 outside it.

    A negative delta means the subgroup is worse than the rest of the dataset,
    which is the number doc 23 asks for: "does this backbone cope with glasses"
    is a question about the *gap*, and an absolute F1 inside the subgroup cannot
    answer it.  Rows whose flag is missing belong to neither side and are
    excluded from both -- a NaN read as False would move an unknown into the
    comparison group and shift the baseline.
    """
    out: Dict[str, Any] = {
        "column": column,
        "measurable": False,
        "n_inside": 0,
        "n_outside": 0,
        "macro_f1_inside": None,
        "macro_f1_outside": None,
        "bottom_recall_inside": None,
        "bottom_recall_outside": None,
        "delta": None,
    }
    if column not in frames.columns:
        return out
    flags = frames[column].astype("boolean")
    inside = frames[flags.fillna(False).to_numpy(dtype=bool)]
    outside = frames[(~flags.fillna(True)).to_numpy(dtype=bool)]
    out["n_inside"] = int(len(inside))
    out["n_outside"] = int(len(outside))
    if inside.empty or outside.empty:
        return out

    scores_inside = frame_metrics(inside[label_col], inside[pred_col])
    scores_outside = frame_metrics(outside[label_col], outside[pred_col])
    out.update(
        {
            "measurable": True,
            "macro_f1_inside": scores_inside["macro_f1"],
            "macro_f1_outside": scores_outside["macro_f1"],
            "bottom_recall_inside": scores_inside["bottom_recall"],
            "bottom_recall_outside": scores_outside["bottom_recall"],
            "delta": scores_inside["macro_f1"] - scores_outside["macro_f1"],
        }
    )
    return out


# --------------------------------------------------------------------------
# One arm
# --------------------------------------------------------------------------


def run_arm(
    df: pd.DataFrame,
    cfg: VisionConfig,
    backbone: str,
    *,
    smooth: bool = False,
) -> Dict[str, Any]:
    """Score one backbone's rows under the shared protocol and summarise them.

    ``df`` must already be restricted to this backbone; the arm is otherwise
    identical to a ``gaze_eval`` run, which is deliberate -- Experiment 1 must
    not have its own scoring path that could drift from the one doc 7 gates.
    """
    report = run_evaluation(df, cfg, smooth=smooth)
    frames = report["frames"]
    glasses = subgroup_delta(frames, "glasses")
    profile = backbone_profile(backbone, cfg)
    version = current_ai_version(cfg, backbone)

    per_user = report["per_user"]
    latency = report["latency"]
    sanity = report["sanity"]["pitch_ordering_summary"]
    sources = report["latency_sources"]
    row: Dict[str, Any] = {
        "backbone": backbone,
        "version": profile["version"],
        "n_frames": report["dataset"]["n_rows_scored"],
        "n_participants": len(report["dataset"]["participants"]),
        "macro_f1": report["frame"]["macro_f1"],
        "bottom_recall": report["frame"]["bottom_recall"],
        "camera_recall": report["frame"]["camera_recall"],
        "uncertain_ratio": report["frame"]["uncertain_ratio"],
        "per_user_f1_mean": per_user.get("macro_f1_mean"),
        "per_user_f1_min": per_user.get("macro_f1_min"),
        "per_user_f1_std": per_user.get("macro_f1_std"),
        "glasses_f1": glasses["macro_f1_inside"],
        "no_glasses_f1": glasses["macro_f1_outside"],
        "glasses_delta": glasses["delta"],
        "calibration_failure_rate": report["calibration"]["failure_rate"],
        "latency_p50_ms": latency["p50"],
        "latency_p95_ms": latency["p95"],
        # The doc 7 latency gate is only meaningful when the number was
        # measured end to end; gaze_eval labels each frame's source, and the
        # dominant one travels with the row so a comparison cannot quietly mix
        # a measured p95 with a reconstructed one.
        "latency_source": max(sources, key=sources.get) if sources else "unmeasured",
        "model_size_mb": profile["model_size_mb"],
        "pitch_ordering": _pitch_verdict(sanity),
        "gate_passed": report["release_gate"]["passed"],
    }
    return {
        "backbone": backbone,
        "row": row,
        "profile": profile,
        "ai_version": version.to_dict()["ai_version"],
        "glasses": glasses,
        "frame": report["frame"],
        "frame_smoothed": report["frame_smoothed"],
        "per_user": per_user,
        "per_user_rows": report["per_user_rows"],
        "latency": latency,
        "latency_sources": sources,
        "calibration": {
            key: value
            for key, value in report["calibration"].items()
            if key != "per_participant"
        },
        "calibration_per_participant": report["calibration"]["per_participant"],
        "buckets": report["buckets"],
        "sanity": report["sanity"],
        "release_gate": report["release_gate"],
        "frames": frames,
    }


def _pitch_verdict(summary: Mapping[str, Any]) -> str:
    """schemas.py sign check rolled into one word for the comparison table."""
    if not summary or not summary.get("n_measured"):
        return "UNMEASURED"
    return "OK" if summary.get("passed") else "INVERTED"


# --------------------------------------------------------------------------
# doc 3-3 selection
# --------------------------------------------------------------------------

#: doc 3-3's veto rows: a backbone that misses either one is not selectable no
#: matter how good its macro F1 is.  The gaze-pitch ordering check
#: (``schemas.py``) is available as a third veto but is **off** by default,
#: because doc 5-2 deliberately treats INVERTED_PITCH as a warning: the summary
#: fails as soon as one participant is inverted, and a single participant with a
#: degenerate calibration would then veto a backbone that is fine for everyone
#: else.  It is always reported; ``--pitch-veto`` makes it binding.
VETO_SPEC: Tuple[Tuple[str, str, str, str], ...] = (
    ("bottom_recall", "bottom_recall", "min", "min_bottom_recall"),
    ("p95_latency_ms", "latency_p95_ms", "max", "max_p95_latency_ms"),
)


def veto_checks(
    row: Mapping[str, Any], gate: ReleaseGateConfig, *, pitch_veto: bool = False
) -> List[Dict[str, Any]]:
    """Per-arm status of every doc 3-3 veto row.

    ``status`` is ``pass`` / ``fail`` / ``unmeasured``; the third is not a pass.
    A backbone whose latency this table never recorded has not demonstrated that
    it fits the doc 3-3 budget, and selecting it would be selecting on missing
    data.
    """
    checks: List[Dict[str, Any]] = []
    for name, column, direction, field_name in VETO_SPEC:
        target = float(getattr(gate, field_name))
        value = row.get(column)
        number = None
        if value is not None and not isinstance(value, bool):
            try:
                candidate = float(value)
            except (TypeError, ValueError):
                candidate = float("nan")
            number = candidate if np.isfinite(candidate) else None
        if number is None:
            status = "unmeasured"
        elif direction == "min":
            status = "pass" if number >= target else "fail"
        else:
            status = "pass" if number <= target else "fail"
        checks.append(
            {
                "check": name,
                "value": number,
                "target": target,
                "direction": direction,
                "status": status,
            }
        )

    if pitch_veto:
        verdict = str(row.get("pitch_ordering") or "UNMEASURED")
        checks.append(
            {
                "check": "pitch_ordering",
                "value": verdict,
                "target": "OK",
                "direction": "equals",
                "status": {"OK": "pass", "INVERTED": "fail"}.get(verdict, "unmeasured"),
            }
        )
    return checks


def _rank_key(row: Mapping[str, Any]) -> Tuple[float, float, float, str]:
    """Highest macro F1, then cheapest: latency, then model size, then name.

    Every tie-break is deterministic on purpose -- two arms that score the same
    must not swap places between runs of the same experiment (doc 18).
    """

    def number(value: Any, fallback: float) -> float:
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            return fallback
        return candidate if np.isfinite(candidate) else fallback

    return (
        -number(row.get("macro_f1"), -1.0),
        number(row.get("latency_p95_ms"), float("inf")),
        number(row.get("model_size_mb"), float("inf")),
        str(row.get("backbone")),
    )


def select_backbone(
    rows: Sequence[Mapping[str, Any]],
    gate: ReleaseGateConfig,
    *,
    pitch_veto: bool = False,
) -> Dict[str, Any]:
    """Apply doc 3-3: rank by macro F1 *inside* the arms no veto row blocks."""
    checks = {str(row["backbone"]): veto_checks(row, gate, pitch_veto=pitch_veto) for row in rows}
    eligible = [
        row
        for row in rows
        if all(check["status"] == "pass" for check in checks[str(row["backbone"])])
    ]
    eligible_names = {str(row["backbone"]) for row in eligible}
    ranked_all = sorted(rows, key=_rank_key)
    ranked_eligible = sorted(eligible, key=_rank_key)
    best_f1 = ranked_all[0] if ranked_all else None
    recommended = ranked_eligible[0] if ranked_eligible else None
    return {
        "rule": (
            "doc 3-3: maximise macro F1 among the backbones that satisfy every veto row "
            f"(bottom_recall >= {gate.min_bottom_recall}, "
            f"p95 latency <= {gate.max_p95_latency_ms} ms"
            + (", gaze-pitch ordering not INVERTED" if pitch_veto else "")
            + "); an unmeasured veto row does not pass. Ties: lower p95, then smaller "
            "model, then name."
        ),
        "checks": checks,
        "eligible": [str(row["backbone"]) for row in ranked_eligible],
        "vetoed": {
            str(row["backbone"]): [
                check["check"]
                for check in checks[str(row["backbone"])]
                if check["status"] != "pass"
            ]
            for row in rows
            if str(row["backbone"]) not in eligible_names
        },
        "recommended": None if recommended is None else str(recommended["backbone"]),
        "best_macro_f1": None if best_f1 is None else str(best_f1["backbone"]),
        "recommended_is_best_macro_f1": (
            recommended is not None and best_f1 is not None and recommended is best_f1
        ),
        "ranking": [str(row["backbone"]) for row in ranked_all],
    }


def verdict_lines(
    selection: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> List[str]:
    """The written verdict doc 23 asks for, stated in full sentences."""
    by_name = {str(row["backbone"]): row for row in rows}
    lines: List[str] = []
    best = selection.get("best_macro_f1")
    recommended = selection.get("recommended")

    # Stated first and independently of the veto setting: an inverted adapter
    # (schemas.py) invalidates the sign convention every other number here is
    # read under, so it must not be a footnote under a recommendation.
    inverted = [str(row["backbone"]) for row in rows if row.get("pitch_ordering") == "INVERTED"]
    if inverted:
        lines.append(
            f"**Gaze-pitch ordering INVERTED for {', '.join(f'`{name}`' for name in inverted)}.** "
            "At least one participant's BOTTOM gaze pitch is not below their CAMERA pitch "
            "(schemas.py). Check the adapter's sign conversion and that participant's "
            "calibration before trusting the comparison"
            + (
                "."
                if "pitch_ordering" in {c["check"] for c in selection["checks"][inverted[0]]}
                else "; it is reported here but is not vetoing (pass --pitch-veto to make it "
                "binding)."
            )
        )

    if best is not None:
        row = by_name[best]
        lines.append(
            f"Highest macro F1: `{best}` at {_num(row.get('macro_f1'))} "
            f"(BOTTOM recall {_num(row.get('bottom_recall'))}, "
            f"p95 {_num(row.get('latency_p95_ms'), 1)} ms, "
            f"per-user F1 min {_num(row.get('per_user_f1_min'))})."
        )

    if recommended is None:
        blocked = ", ".join(
            f"`{name}` ({', '.join(reasons)})"
            for name, reasons in sorted(selection.get("vetoed", {}).items())
        )
        lines.append(
            "**No backbone is selectable.** Every arm is blocked by a doc 3-3 veto row: "
            f"{blocked or 'no arms were scored'}. Fix the blocking row before reading the "
            "F1 column as a decision."
        )
        return lines

    row = by_name[recommended]
    if selection.get("recommended_is_best_macro_f1"):
        lines.append(
            f"**Recommended: `{recommended}`.** It has both the best macro F1 and a clean "
            "sweep of the doc 3-3 veto rows, so the two criteria agree and no trade-off "
            "had to be made."
        )
    else:
        reasons = ", ".join(selection.get("vetoed", {}).get(str(best), [])) or "a veto row"
        lines.append(
            f"**Recommended: `{recommended}`.** `{best}` scores higher on macro F1 "
            f"({_num(by_name[best].get('macro_f1'))} vs {_num(row.get('macro_f1'))}) but is "
            f"vetoed on {reasons}; doc 3-3 lets BOTTOM recall and latency override a better "
            f"F1, so the selection is `{recommended}` at macro F1 {_num(row.get('macro_f1'))}, "
            f"BOTTOM recall {_num(row.get('bottom_recall'))}, p95 "
            f"{_num(row.get('latency_p95_ms'), 1)} ms."
        )

    spread = row.get("per_user_f1_std")
    if spread is not None and np.isfinite(float(spread)):
        lines.append(
            f"Per-user spread for `{recommended}`: mean {_num(row.get('per_user_f1_mean'))}, "
            f"min {_num(row.get('per_user_f1_min'))}, std {_num(spread)}. doc 7 gates the "
            "minimum, so a high mean over a low minimum is one destroyed calibration, not a "
            "good model."
        )
    delta = row.get("glasses_delta")
    if delta is None:
        lines.append(
            "Glasses subgroup: not measurable on this table (one of the two groups is "
            "empty). doc 19 keeps glasses as a named failure bucket, so this gap is still "
            "open."
        )
    else:
        direction = "worse" if float(delta) < 0 else "better"
        lines.append(
            f"Glasses subgroup for `{recommended}`: macro F1 {_num(row.get('glasses_f1'))} "
            f"with glasses vs {_num(row.get('no_glasses_f1'))} without, "
            f"{_num(abs(float(delta)))} {direction} (doc 19 glasses bucket)."
        )

    unmeasured = sorted(
        {
            check["check"]
            for name in selection.get("checks", {})
            for check in selection["checks"][name]
            if check["status"] == "unmeasured"
        }
    )
    if unmeasured:
        lines.append(
            f"Unmeasured veto rows on at least one arm: {', '.join(unmeasured)}. Those arms "
            "were excluded from selection rather than given the benefit of the doubt."
        )
    return lines


def _num(value: Any, digits: int = 4) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{number:.{digits}f}" if np.isfinite(number) else "-"


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def comparison_frame(arms: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([arm["row"] for arm in arms], columns=list(COMPARISON_COLUMNS))


def rank_buckets(
    buckets: Sequence[Mapping[str, Any]], limit: int = 5
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Worst doc 19 buckets, with the single-class ones ranked separately.

    ``buckets.py`` defines two buckets against the ground-truth label ("head
    down but eyes on camera", "eyes down but head straight"), so they contain
    one class only and the absent class scores 0 recall by construction.  Their
    macro F1 is therefore capped near 0.5 and they would occupy the top of any
    "worst first" list on every run, in every dataset, whatever the model did.
    They get their own table, ranked on the recall of the class they actually
    contain -- which is the number that means something for them.

    Returns ``(two_class_worst, single_class_worst)``, both ascending in
    badness-first order and truncated to ``limit``.
    """
    two_class: List[Dict[str, Any]] = []
    single_class: List[Dict[str, Any]] = []
    for row in buckets:
        if row["bucket"] == "ALL" or not row["n"]:
            continue
        n_camera = float(row.get("n_camera") or 0.0)
        n_bottom = float(row.get("n_bottom") or 0.0)
        if n_camera and n_bottom:
            two_class.append(dict(row))
            continue
        present = "CAMERA" if n_camera else "BOTTOM"
        single_class.append(
            {
                **row,
                "class": present,
                "recall": row.get("camera_recall") if n_camera else row.get("bottom_recall"),
            }
        )
    two_class.sort(key=lambda row: (row.get("macro_f1_delta") or 0.0))
    single_class.sort(key=lambda row: (row.get("recall") if row.get("recall") is not None else 0.0))
    return two_class[:limit], single_class[:limit]


def render_markdown(report: Mapping[str, Any], run_name: str) -> str:
    run = report["run"]
    selection = report["selection"]
    lines: List[str] = [
        f"# {EXPERIMENT} - {run_name}",
        "",
        f"doc 23 Experiment 1. Backbones: {', '.join(run['backbones'])}. "
        f"Split `{run['split']}`, feature set `{run['feature_set']}`, "
        f"temporal smoothing {'on' if run['temporal'] else 'off'}.",
        "",
        f"- dataset: `{report['dataset']['version']}` "
        f"({report['dataset']['n_rows']} rows, {report['dataset']['n_participants']} participants)",
        f"- config hash: `{run['config_hash']}`  commit: `{run['code_commit']}`  "
        f"thresholds: p_max {run['thresholds']['p_max_threshold']}, "
        f"margin {run['thresholds']['margin_threshold']}",
        "",
        "## Verdict (doc 3-3)",
        "",
        selection["rule"],
        "",
    ]
    lines += [f"- {line}" for line in report["verdict"]]
    lines += [
        "",
        "## Comparison",
        "",
        md_table([arm["row"] for arm in report["arms"]], list(COMPARISON_COLUMNS)),
        "",
        "## Veto rows per backbone",
        "",
        md_table(
            [
                {"backbone": name, **check}
                for name, checks in selection["checks"].items()
                for check in checks
            ],
            ["backbone", "check", "value", "target", "direction", "status"],
        ),
        "",
        "## Frame alignment",
        "",
    ]
    alignment = report["dataset"]["alignment"]
    if alignment.get("aligned"):
        lines.append(
            f"{alignment['n_common']} sample ids are common to every arm; "
            + (
                "the arms measured exactly the same frames."
                if alignment.get("identical")
                else "arms were restricted to the intersection."
            )
        )
        lines += [
            "",
            md_table(
                [
                    {"backbone": name, **counts}
                    for name, counts in alignment["per_backbone"].items()
                ],
                ["backbone", "n", "n_dropped"],
            ),
        ]
    else:
        lines.append(f"_not aligned: {alignment.get('note')}_")

    lines += ["", "## Per-user F1", ""]
    per_user_rows: List[Dict[str, Any]] = []
    for arm in report["arms"]:
        for row in arm["per_user_rows"]:
            per_user_rows.append(
                {
                    "backbone": arm["backbone"],
                    "participant_id": row.get("participant_id"),
                    "n_labelled": row.get("n_labelled"),
                    "macro_f1": row.get("macro_f1"),
                    "bottom_recall": row.get("bottom_recall"),
                    "uncertain_ratio": row.get("uncertain_ratio"),
                }
            )
    lines.append(
        md_table(
            per_user_rows,
            [
                "backbone",
                "participant_id",
                "n_labelled",
                "macro_f1",
                "bottom_recall",
                "uncertain_ratio",
            ],
        )
    )

    lines += [
        "",
        "## doc 19 buckets, worst first",
        "",
        "Single-class buckets are listed apart: `buckets.py` defines two of them against the "
        "ground-truth label, so the absent class scores 0 recall by construction and their "
        "macro F1 is not comparable with the rest. They are ranked on the recall of the "
        "class they actually contain.",
        "",
    ]
    for arm in report["arms"]:
        two_class, single_class = rank_buckets(arm["buckets"])
        lines += [
            f"### `{arm['backbone']}`",
            "",
            md_table(
                two_class,
                ["bucket", "n", "macro_f1", "macro_f1_delta", "bottom_recall", "uncertain_ratio"],
            ),
            "",
            md_table(
                single_class, ["bucket", "n", "class", "recall", "uncertain_ratio"]
            ),
            "",
        ]

    lines += [
        "## Backbone build state",
        "",
        md_table(
            [arm["profile"] for arm in report["arms"]],
            ["backbone", "version", "weights", "model_size_mb", "checkpoint", "note"],
        ),
        "",
        "`weights: unavailable` means this machine has no checkpoint for that backbone, so "
        "the model size could not be read; the scores still come from the stored table, "
        "which was produced when the weights were present.",
    ]
    return "\n".join(lines) + "\n"


def write_report(
    report: Mapping[str, Any],
    out_dir: Path,
    run_name: str,
    *,
    arms: Sequence[Mapping[str, Any]] = (),
    dump_frames: bool = False,
) -> Dict[str, Path]:
    """Write ``exp1_backbone.{json,csv,md}`` and optionally the scored frames."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: Dict[str, Path] = {}

    csv_path = out_dir / "exp1_backbone.csv"
    pd.DataFrame([arm["row"] for arm in report["arms"]], columns=list(COMPARISON_COLUMNS)).to_csv(
        csv_path, index=False
    )
    written["csv"] = csv_path

    json_path = out_dir / "exp1_backbone.json"
    json_path.write_text(json.dumps(jsonable(report), indent=2), encoding="utf-8")
    written["json"] = json_path

    md_path = out_dir / "exp1_backbone.md"
    md_path.write_text(render_markdown(report, run_name), encoding="utf-8")
    written["markdown"] = md_path

    if dump_frames:
        for arm in arms:
            written[f"frames_{arm['backbone']}"] = save_table(
                arm["frames"], out_dir / f"scored_frames_{arm['backbone']}.parquet"
            )
    return written


def failure_case_links(arm: Mapping[str, Any], md_path: Path) -> List[Dict[str, str]]:
    """doc 18 failure-case links: the doc 19 buckets this arm did worst in."""
    two_class, single_class = rank_buckets(arm["buckets"], limit=3)
    anchor = f"{md_path.as_posix()}#doc-19-buckets-worst-first"
    links = [
        {
            "label": f"bucket:{row['bucket']} n={row['n']} macro_f1={_num(row.get('macro_f1'))} "
            f"(delta {_num(row.get('macro_f1_delta'))})",
            "ref": anchor,
        }
        for row in two_class
    ]
    links += [
        {
            "label": f"bucket:{row['bucket']} n={row['n']} {row['class']} recall="
            f"{_num(row.get('recall'))}",
            "ref": anchor,
        }
        for row in single_class[:2]
    ]
    inverted = arm["sanity"]["pitch_ordering_summary"].get("inverted") or []
    if inverted:
        links.append(
            {
                "label": f"INVERTED gaze pitch on {len(inverted)} participant(s) (schemas.py)",
                "ref": f"{md_path.as_posix()}#comparison",
            }
        )
    return links


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exp1_backbone",
        description=(
            "doc 23 Experiment 1: compare gaze backbones on one feature table under a fixed "
            "protocol and apply the doc 3-3 selection rule. Writes "
            "ai/reports/<run>/exp1_backbone.{csv,json,md} and appends to the doc 18 log."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--table", action="append", default=[], help="feature table; repeatable")
    parser.add_argument("--features-dir", default=None, help="directory of feature tables")
    parser.add_argument(
        "--backbone",
        action="append",
        default=[],
        help="restrict to these backbones; repeatable (default: every one in the table)",
    )
    parser.add_argument("--config-dir", default=None, help="override ai/configs")
    parser.add_argument("--run-name", default=None, help="report folder name")
    parser.add_argument("--out-dir", default=None, help="override ai/reports/<run>")
    parser.add_argument(
        "--split",
        choices=("all", "train", "val", "test"),
        default="all",
        help="doc 4-3 participant split; 'all' uses every participant in the table",
    )
    parser.add_argument("--split-seed", type=int, default=42)
    parser.add_argument("--split-ratios", default="0.6,0.2,0.2")
    parser.add_argument(
        "--temporal",
        action="store_true",
        help="also run the doc 6 smoother (off by default: it masks backbone jitter)",
    )
    parser.add_argument(
        "--no-align-frames",
        action="store_true",
        help="compare the arms on their own frames instead of the shared intersection",
    )
    parser.add_argument(
        "--pitch-veto",
        action="store_true",
        help=(
            "let an INVERTED gaze-pitch ordering veto a backbone; off by default because "
            "doc 5-2 treats it as a warning and one bad participant fails the whole check"
        ),
    )
    parser.add_argument("--dump-frames", action="store_true", help="write the scored frames")
    parser.add_argument("--log", default=str(DEFAULT_LOG_PATH), help="doc 18 JSONL log")
    parser.add_argument("--no-log", action="store_true", help="do not append to the doc 18 log")
    parser.add_argument(
        "--timestamp",
        default=None,
        help="UTC timestamp for the log records (default: now); set it to re-create a record",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(Path(args.config_dir) if args.config_dir else None)

    paths = resolve_tables(args.table, args.features_dir)
    df = load_tables(paths)
    if "backbone" not in df.columns:
        raise SystemExit("the feature table has no backbone column; nothing to compare")

    available = sorted({str(name) for name in df["backbone"].dropna().unique()})
    backbones = [str(name).strip().lower() for name in args.backbone] or available
    missing = [name for name in backbones if name not in available]
    if missing:
        raise SystemExit(f"backbone(s) {missing} are not in the table; have {available}")
    if len(backbones) < 2:
        print(
            f"warning: only one backbone ({backbones}) is present, so this is a single-arm "
            "run, not a comparison. Extract with --backbone twice to compare.",
            file=sys.stderr,
        )

    split_ids: Dict[str, List[str]] = {}
    if args.split != "all":
        ratios = tuple(float(part) for part in args.split_ratios.split(","))
        split_ids = participant_split(df["participant_id"].unique(), ratios, args.split_seed)
        df = df[df["participant_id"].isin(split_ids[args.split])]
        if df.empty:
            raise SystemExit(f"split {args.split!r} is empty for these participants")
    else:
        split_ids = {"all": sorted({str(pid) for pid in df["participant_id"].dropna().unique()})}

    if args.no_align_frames:
        aligned, alignment = df, {
            "aligned": None,
            "note": "--no-align-frames: each arm keeps its own frames",
            "per_backbone": {},
        }
    else:
        aligned, alignment = align_frames(df, backbones)
        if alignment.get("aligned") is False:
            raise SystemExit(str(alignment.get("note")))
        if alignment.get("aligned") is None:
            print(f"warning: {alignment.get('note')}", file=sys.stderr)

    arms: List[Dict[str, Any]] = []
    for name in backbones:
        subset = aligned[aligned["backbone"] == name]
        if subset.empty:
            raise SystemExit(f"backbone {name!r} has no rows after alignment")
        arms.append(run_arm(subset, cfg, name, smooth=args.temporal))

    rows = [arm["row"] for arm in arms]
    selection = select_backbone(rows, cfg.release_gate, pitch_veto=args.pitch_veto)

    timestamp = args.timestamp or now_utc_iso()
    run_name = args.run_name or f"exp1_backbone_{timestamp.replace(':', '').replace('-', '')[:15]}"
    out_dir = Path(args.out_dir) if args.out_dir else REPORTS_DIR / run_name
    fingerprint = dataset_fingerprint(aligned)

    report: Dict[str, Any] = {
        "run": {
            "experiment": EXPERIMENT,
            "name": run_name,
            "timestamp_utc": timestamp,
            "tables": [str(path) for path in paths],
            "backbones": backbones,
            "split": args.split,
            "split_participants": split_ids,
            "feature_set": cfg.calibration.feature_set,
            "thresholds": {
                "p_max_threshold": cfg.calibration.p_max_threshold,
                "margin_threshold": cfg.calibration.margin_threshold,
            },
            "temporal": bool(args.temporal),
            "config_hash": cfg.hash(),
            "code_commit": git_commit(),
        },
        "dataset": {**fingerprint, "alignment": alignment},
        "arms": [{key: value for key, value in arm.items() if key != "frames"} for arm in arms],
        "selection": selection,
        "verdict": verdict_lines(selection, rows),
    }

    written = write_report(report, out_dir, run_name, arms=arms, dump_frames=args.dump_frames)

    if not args.no_log:
        records = [
            ExperimentRecord(
                experiment=EXPERIMENT,
                variant=arm["backbone"],
                experiment_id=make_experiment_id(
                    EXPERIMENT, timestamp, cfg.hash(), arm["backbone"]
                ),
                timestamp_utc=timestamp,
                code_commit=report["run"]["code_commit"],
                config_hash=cfg.hash(),
                dataset_version=fingerprint["version"],
                model_version=f"{MODEL_VERSION}/{arm['backbone']}:{arm['profile']['version']}",
                backbone=arm["backbone"],
                feature_set=cfg.calibration.feature_set,
                thresholds=report["run"]["thresholds"],
                participant_split=split_ids,
                metrics={
                    **{
                        key: arm["row"][key]
                        for key in (
                            "macro_f1",
                            "bottom_recall",
                            "camera_recall",
                            "uncertain_ratio",
                            "per_user_f1_mean",
                            "per_user_f1_min",
                            "per_user_f1_std",
                            "glasses_delta",
                            "calibration_failure_rate",
                            "model_size_mb",
                            "pitch_ordering",
                        )
                    },
                    "gate_passed": arm["row"]["gate_passed"],
                    "recommended": selection["recommended"] == arm["backbone"],
                    "vetoed_on": selection["vetoed"].get(arm["backbone"], []),
                },
                latency={**arm["latency"], "source": arm["row"]["latency_source"]},
                failure_cases=failure_case_links(arm, written["markdown"]),
                artefacts={key: str(path) for key, path in written.items()},
                notes=(
                    f"doc 23 Experiment 1 arm; temporal={'on' if args.temporal else 'off'}; "
                    f"frames aligned across arms={alignment.get('aligned')}"
                ),
            )
            for arm in arms
        ]
        log_paths = update_log(records, args.log, generated_utc=timestamp)
        written.update({f"log_{key}": path for key, path in log_paths.items()})

    print(
        f"exp1: {len(arms)} backbone arm(s) over {fingerprint['n_rows']} rows "
        f"({fingerprint['n_participants']} participants, split={args.split})"
    )
    print(
        comparison_frame(arms)[
            [
                "backbone",
                "macro_f1",
                "bottom_recall",
                "per_user_f1_min",
                "latency_p95_ms",
                "model_size_mb",
                "gate_passed",
            ]
        ].to_string(index=False)
    )
    for line in report["verdict"]:
        print(f"  - {line}")
    for name, path in written.items():
        print(f"  {name}: {path}")
    # A run with nothing selectable is a real outcome, not a crash, but CI should
    # be able to branch on it.
    return 0 if selection["recommended"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
