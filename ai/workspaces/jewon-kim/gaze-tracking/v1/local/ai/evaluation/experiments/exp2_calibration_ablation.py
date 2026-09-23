"""doc 23 Experiment 2 -- what each calibration feature group is worth (doc 5-1).

Three feature sets, one protocol, one backbone::

    A   gaze yaw/pitch only                     2 dims
    B   A + head yaw/pitch/roll                 5 dims
    C   B + gaze deltas from both centroids     9 dims   <- shipped default

The ablation runs the doc 4-3 per-user protocol unchanged for each set: the
first calibration block fits that user's doc 5-3 logistic regression, everything
after it is scored, and no frame ever crosses between the two
(``vision.data.splits.per_participant_partition`` raises if one does).  Only
``calibration.feature_set`` moves between arms, which is also why the config
hash differs per arm and is recorded per arm in the doc 18 log.

Two numbers are reported per set because doc 7 gates both and they trade against
each other:

``per-user F1`` (mean **and** min)
    the gate is on the minimum.  A richer feature set that lifts the mean while
    destroying one user has not improved anything the release gate cares about,
    and with ~32 calibration rows a 9-dim set has real room to overfit one
    person's held pose.
``calibration failure rate``
    doc 5-2's pre-flight check runs on the same features, so a feature set also
    changes how often a user is sent back to recalibrate.  A set that scores
    well only on the users it did not reject is not better, and the failure rate
    is what makes that visible.

Everything else is held fixed, including the doc 5-4 thresholds and the absence
of temporal smoothing (doc 6 would smooth over exactly the per-frame instability
this experiment is measuring).
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

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

from vision.calibration.features import FEATURE_SETS, normalise_feature_set  # noqa: E402
from vision.config import REPORTS_DIR, ReleaseGateConfig, VisionConfig, load_config  # noqa: E402
from vision.data.features_table import save_table  # noqa: E402
from vision.data.splits import participant_split  # noqa: E402
from vision.runtime.version import MODEL_VERSION, git_commit  # noqa: E402

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

EXPERIMENT = "exp2_calibration_ablation"

#: Arms, in the order doc 23 lists them (smallest set first, so the table reads
#: as "what does adding this group buy").
DEFAULT_FEATURE_SETS: Sequence[str] = ("A", "B", "C")

COMPARISON_COLUMNS: Sequence[str] = (
    "feature_set",
    "n_features",
    "features",
    "n_participants",
    "n_frames",
    "macro_f1",
    "bottom_recall",
    "uncertain_ratio",
    "per_user_f1_mean",
    "per_user_f1_min",
    "per_user_f1_std",
    "calibration_failure_rate",
    "n_calibration_failed",
    "loo_accuracy_mean",
    "separability_mean",
    "gate_passed",
)


# --------------------------------------------------------------------------
# One arm
# --------------------------------------------------------------------------


def config_for_feature_set(cfg: VisionConfig, feature_set: str) -> VisionConfig:
    """A copy of ``cfg`` with only ``calibration.feature_set`` changed.

    Deep-copied rather than mutated in place: the arms are scored in a loop and
    a shared config object would make every arm's recorded ``config_hash`` the
    hash of whichever set ran last (doc 18).
    """
    arm = copy.deepcopy(cfg)
    arm.calibration.feature_set = normalise_feature_set(feature_set)
    return arm


def _mean_of(records: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
    """Mean over the participants where the statistic exists.

    ``None`` when nobody produced one: a calibration that could not be fitted
    has no LOO accuracy, and averaging it in as 0.0 would report a real number
    for something never measured.
    """
    values = [
        float(record[key])
        for record in records
        if record.get(key) is not None and np.isfinite(float(record[key]))
    ]
    return float(np.mean(values)) if values else None


def run_arm(
    df: pd.DataFrame,
    cfg: VisionConfig,
    feature_set: str,
    *,
    smooth: bool = False,
) -> Dict[str, Any]:
    """Score one feature set under the doc 4-3 per-user protocol."""
    key = normalise_feature_set(feature_set)
    arm_cfg = config_for_feature_set(cfg, key)
    report = run_evaluation(df, arm_cfg, smooth=smooth)

    calibration = report["calibration"]
    per_participant = calibration["per_participant"]
    per_user = report["per_user"]
    names = list(FEATURE_SETS[key])
    row: Dict[str, Any] = {
        "feature_set": key,
        "n_features": len(names),
        # Joined with "+" rather than ", ": this column lands in a CSV, and a
        # comma inside a cell survives a real reader but not the `cut`/`awk`
        # look a human takes at a report.
        "features": " + ".join(names),
        "n_participants": calibration["n_participants"],
        "n_frames": report["dataset"]["n_rows_scored"],
        "macro_f1": report["frame"]["macro_f1"],
        "bottom_recall": report["frame"]["bottom_recall"],
        "uncertain_ratio": report["frame"]["uncertain_ratio"],
        "per_user_f1_mean": per_user.get("macro_f1_mean"),
        "per_user_f1_min": per_user.get("macro_f1_min"),
        "per_user_f1_std": per_user.get("macro_f1_std"),
        "calibration_failure_rate": calibration["failure_rate"],
        "n_calibration_failed": calibration["n_failed"],
        "loo_accuracy_mean": _mean_of(per_participant, "loo_accuracy"),
        "separability_mean": _mean_of(per_participant, "separability"),
        "gate_passed": report["release_gate"]["passed"],
    }
    return {
        "feature_set": key,
        "row": row,
        "feature_names": names,
        "config_hash": arm_cfg.hash(),
        "frame": report["frame"],
        "per_user": per_user,
        "per_user_rows": report["per_user_rows"],
        "latency": report["latency"],
        "calibration": {
            key_: value for key_, value in calibration.items() if key_ != "per_participant"
        },
        "calibration_per_participant": per_participant,
        "calibration_reasons": _reason_counts(per_participant),
        "buckets": report["buckets"],
        "sanity": report["sanity"],
        "release_gate": report["release_gate"],
        "frames": report["frames"],
    }


def _reason_counts(per_participant: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    """doc 5-2 fail reasons, counted -- the ablation's diagnosis of a bad arm."""
    tally: Dict[str, int] = {}
    for record in per_participant:
        if not record.get("failed"):
            continue
        reason = str(record.get("reason") or "UNFITTED")
        tally[reason] = tally.get(reason, 0) + 1
    return dict(sorted(tally.items(), key=lambda item: (-item[1], item[0])))


# --------------------------------------------------------------------------
# Cross-arm views
# --------------------------------------------------------------------------


def per_user_matrix(arms: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Per-participant macro F1, one column per feature set, plus the winner.

    The pooled numbers can hide the thing the ablation is for: set C usually
    wins on average while losing on the one user whose calibration it overfits,
    and only a per-user table shows which user that was.
    """
    rows: Dict[str, Dict[str, Any]] = {}
    for arm in arms:
        for record in arm["per_user_rows"]:
            participant = str(record.get("participant_id"))
            row = rows.setdefault(participant, {"participant_id": participant})
            row[f"f1_{arm['feature_set']}"] = record.get("macro_f1")
            row[f"uncertain_{arm['feature_set']}"] = record.get("uncertain_ratio")
            row.setdefault("n_labelled", record.get("n_labelled"))

    keys = [f"f1_{arm['feature_set']}" for arm in arms]
    for row in rows.values():
        scored = {
            key: float(row[key])
            for key in keys
            if row.get(key) is not None and np.isfinite(float(row[key]))
        }
        row["best_set"] = (
            max(scored, key=lambda key: (scored[key], -keys.index(key)))[len("f1_") :]
            if scored
            else None
        )
        row["spread"] = (max(scored.values()) - min(scored.values())) if scored else None
    columns = ["participant_id", "n_labelled", *keys, "best_set", "spread"]
    return pd.DataFrame(list(rows.values()), columns=columns).sort_values(
        "participant_id", kind="stable"
    )


def select_feature_set(
    rows: Sequence[Mapping[str, Any]], gate: ReleaseGateConfig
) -> Dict[str, Any]:
    """Pick the feature set doc 7 would ship.

    The gate is on the per-user *minimum* F1 and on the calibration failure
    rate, so those two are the criteria here, in that order: among the sets
    whose failure rate is within budget, take the highest per-user minimum, then
    the highest mean, then the *smallest* set.  The last tie-break is not
    cosmetic -- with four seconds of calibration the cheapest set that ties is
    the one least able to memorise a held pose (doc 5-1).
    """
    def key(row: Mapping[str, Any]):
        def number(value: Any, fallback: float) -> float:
            try:
                candidate = float(value)
            except (TypeError, ValueError):
                return fallback
            return candidate if np.isfinite(candidate) else fallback

        return (
            -number(row.get("per_user_f1_min"), -1.0),
            -number(row.get("per_user_f1_mean"), -1.0),
            number(row.get("n_features"), float("inf")),
            str(row.get("feature_set")),
        )

    budget = float(gate.max_calibration_failure_rate)
    feasible = [
        row
        for row in rows
        if row.get("calibration_failure_rate") is not None
        and float(row["calibration_failure_rate"]) <= budget
    ]
    feasible_names = {str(row["feature_set"]) for row in feasible}
    ranked_all = sorted(rows, key=key)
    ranked_feasible = sorted(feasible, key=key)
    return {
        "rule": (
            "doc 7: among the feature sets whose calibration failure rate is <= "
            f"{budget}, maximise the per-user minimum macro F1, then the per-user mean, "
            "then prefer the smaller set."
        ),
        "max_calibration_failure_rate": budget,
        "feasible": [str(row["feature_set"]) for row in ranked_feasible],
        "rejected": {
            str(row["feature_set"]): row.get("calibration_failure_rate")
            for row in rows
            if str(row["feature_set"]) not in feasible_names
        },
        "recommended": (
            str(ranked_feasible[0]["feature_set"]) if ranked_feasible else None
        ),
        "best_per_user_min": str(ranked_all[0]["feature_set"]) if ranked_all else None,
        "ranking": [str(row["feature_set"]) for row in ranked_all],
    }


def verdict_lines(
    selection: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    configured: str,
) -> List[str]:
    """The written verdict for doc 23 Experiment 2."""
    by_set = {str(row["feature_set"]): row for row in rows}
    lines: List[str] = []

    for row in rows:
        lines.append(
            f"Set `{row['feature_set']}` ({row['n_features']} dims): per-user F1 mean "
            f"{_num(row.get('per_user_f1_mean'))} / min {_num(row.get('per_user_f1_min'))}, "
            f"pooled macro F1 {_num(row.get('macro_f1'))}, BOTTOM recall "
            f"{_num(row.get('bottom_recall'))}, calibration failure rate "
            f"{_num(row.get('calibration_failure_rate'), 3)} "
            f"({row.get('n_calibration_failed')} of {row.get('n_participants')})."
        )

    recommended = selection.get("recommended")
    if recommended is None:
        rejected = ", ".join(
            f"`{name}` ({_num(rate, 3)})" for name, rate in sorted(selection["rejected"].items())
        )
        lines.append(
            "**No feature set is shippable.** Every arm exceeds the doc 7 calibration "
            f"failure budget of {selection['max_calibration_failure_rate']}: {rejected}. "
            "Fix calibration before choosing features."
        )
        return lines

    row = by_set[recommended]
    lines.append(
        f"**Recommended: set `{recommended}`.** {selection['rule']} It wins on the gated "
        f"number (per-user min {_num(row.get('per_user_f1_min'))})."
    )
    best_min = selection.get("best_per_user_min")
    if best_min and best_min != recommended:
        lines.append(
            f"Set `{best_min}` has the better per-user minimum "
            f"({_num(by_set[best_min].get('per_user_f1_min'))}) but its calibration failure "
            f"rate {_num(by_set[best_min].get('calibration_failure_rate'), 3)} is over the doc 7 "
            f"budget of {selection['max_calibration_failure_rate']}."
        )
    if recommended != configured:
        lines.append(
            f"This disagrees with `ai/configs/calibration.yaml`, which is set to "
            f"`{configured}`. Change it only on a dataset large enough to carry the "
            "difference -- doc 4-3 splits people, and a handful of participants moves a "
            "per-user minimum by one person."
        )
    else:
        lines.append(f"This matches the configured `feature_set: {configured}`.")

    spreads = {
        str(row_["feature_set"]): (
            None
            if row_.get("per_user_f1_std") is None
            else float(row_["per_user_f1_std"])
        )
        for row_ in rows
    }
    widest = max(
        (name for name, value in spreads.items() if value is not None),
        key=lambda name: spreads[name],
        default=None,
    )
    if widest is not None:
        lines.append(
            f"Widest per-user spread: set `{widest}` (std {_num(spreads[widest])}). A set is "
            "only worth its extra dimensions if it lifts the worst user, not the mean."
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


def render_markdown(report: Mapping[str, Any], run_name: str) -> str:
    run = report["run"]
    selection = report["selection"]
    lines: List[str] = [
        f"# {EXPERIMENT} - {run_name}",
        "",
        f"doc 23 Experiment 2. Feature sets: {', '.join(run['feature_sets'])}. "
        f"Backbone `{run['backbone']}`, split `{run['split']}`, "
        f"temporal smoothing {'on' if run['temporal'] else 'off'}.",
        "",
        f"- dataset: `{report['dataset']['version']}` "
        f"({report['dataset']['n_rows']} rows, {report['dataset']['n_participants']} participants)",
        f"- commit: `{run['code_commit']}`  thresholds: "
        f"p_max {run['thresholds']['p_max_threshold']}, "
        f"margin {run['thresholds']['margin_threshold']}",
        "- protocol: doc 4-3 per user -- fit on the first calibration block, score everything "
        "after it; only `calibration.feature_set` differs between arms.",
        "",
        "## Verdict",
        "",
    ]
    lines += [f"- {line}" for line in report["verdict"]]
    lines += [
        "",
        "## Feature sets",
        "",
        md_table([arm["row"] for arm in report["arms"]], list(COMPARISON_COLUMNS)),
        "",
        "## Per-user macro F1",
        "",
        md_table(report["per_user_matrix"], list(report["per_user_matrix_columns"])),
        "",
        "## Calibration failures (doc 5-2)",
        "",
        md_table(
            [
                {
                    "feature_set": arm["feature_set"],
                    "n_failed": arm["row"]["n_calibration_failed"],
                    "failure_rate": arm["row"]["calibration_failure_rate"],
                    "reasons": ", ".join(
                        f"{reason}x{count}" for reason, count in arm["calibration_reasons"].items()
                    )
                    or "-",
                    "loo_accuracy_mean": arm["row"]["loo_accuracy_mean"],
                    "separability_mean": arm["row"]["separability_mean"],
                }
                for arm in report["arms"]
            ],
            [
                "feature_set",
                "n_failed",
                "failure_rate",
                "reasons",
                "loo_accuracy_mean",
                "separability_mean",
            ],
        ),
        "",
        "## Release gate per set (doc 7)",
        "",
    ]
    for arm in report["arms"]:
        lines += [
            f"### Set `{arm['feature_set']}`",
            "",
            md_table(
                arm["release_gate"]["rows"], ["metric", "value", "target", "direction", "pass"]
            ),
            "",
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
    """Write ``exp2_calibration_ablation.{json,csv,md}`` and optional frames."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: Dict[str, Path] = {}

    csv_path = out_dir / "exp2_calibration_ablation.csv"
    pd.DataFrame([arm["row"] for arm in report["arms"]], columns=list(COMPARISON_COLUMNS)).to_csv(
        csv_path, index=False
    )
    written["csv"] = csv_path

    per_user_path = out_dir / "exp2_per_user_f1.csv"
    pd.DataFrame(
        report["per_user_matrix"], columns=list(report["per_user_matrix_columns"])
    ).to_csv(per_user_path, index=False)
    written["per_user_csv"] = per_user_path

    json_path = out_dir / "exp2_calibration_ablation.json"
    json_path.write_text(json.dumps(jsonable(report), indent=2), encoding="utf-8")
    written["json"] = json_path

    md_path = out_dir / "exp2_calibration_ablation.md"
    md_path.write_text(render_markdown(report, run_name), encoding="utf-8")
    written["markdown"] = md_path

    if dump_frames:
        for arm in arms:
            written[f"frames_{arm['feature_set']}"] = save_table(
                arm["frames"], out_dir / f"scored_frames_set{arm['feature_set']}.parquet"
            )
    return written


def failure_case_links(arm: Mapping[str, Any], md_path: Path) -> List[Dict[str, str]]:
    """doc 18 failure-case links: who this feature set failed, and where."""
    links: List[Dict[str, str]] = [
        {
            "label": f"calibration RETRY_REQUIRED {record['participant_id']} "
            f"({record.get('reason')}, loo={_num(record.get('loo_accuracy'), 3)})",
            "ref": f"{md_path.as_posix()}#calibration-failures-doc-5-2",
        }
        for record in arm["calibration_per_participant"]
        if record.get("failed")
    ]
    worst = sorted(
        (
            record
            for record in arm["per_user_rows"]
            if record.get("macro_f1") is not None
        ),
        key=lambda record: float(record["macro_f1"]),
    )[:2]
    links += [
        {
            "label": f"worst user {record['participant_id']} macro_f1="
            f"{_num(record.get('macro_f1'))}",
            "ref": f"{md_path.as_posix()}#per-user-macro-f1",
        }
        for record in worst
    ]
    return links


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def resolve_backbone(df: pd.DataFrame, requested: Optional[str], cfg: VisionConfig) -> str:
    """Pick the single backbone this ablation runs on.

    A table concatenated from several backbones would otherwise be scored as one
    dataset, and the arms would differ in feature set *and* in the model that
    produced the angles -- a confound that makes the whole experiment
    unreadable.  With several present and no choice made, the config's backbone
    wins if it is in the table; otherwise the run stops and asks.
    """
    if "backbone" not in df.columns:
        return str(cfg.backbone.name)
    available = sorted({str(name) for name in df["backbone"].dropna().unique()})
    if requested:
        key = str(requested).strip().lower()
        if key not in available:
            raise SystemExit(f"backbone {key!r} is not in the table; have {available}")
        return key
    if len(available) == 1:
        return available[0]
    configured = str(cfg.backbone.name).strip().lower()
    if configured in available:
        return configured
    raise SystemExit(
        f"the table holds several backbones {available} and none matches the configured "
        f"{configured!r}; pass --backbone to choose one (mixing them would confound the "
        "feature-set comparison)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exp2_calibration_ablation",
        description=(
            "doc 23 Experiment 2: calibration feature sets A/B/C under the doc 4-3 per-user "
            "protocol. Writes ai/reports/<run>/exp2_calibration_ablation.{csv,json,md} and "
            "appends to the doc 18 log."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--table", action="append", default=[], help="feature table; repeatable")
    parser.add_argument("--features-dir", default=None, help="directory of feature tables")
    parser.add_argument(
        "--feature-set",
        action="append",
        default=[],
        choices=sorted(FEATURE_SETS),
        help="restrict to these sets; repeatable (default: A, B, C)",
    )
    parser.add_argument(
        "--backbone",
        default=None,
        help="which backbone's rows to use when the table holds more than one",
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
        help="also run the doc 6 smoother (off by default: it hides per-frame instability)",
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
    feature_sets = [normalise_feature_set(key) for key in args.feature_set] or list(
        DEFAULT_FEATURE_SETS
    )

    paths = resolve_tables(args.table, args.features_dir)
    df = load_tables(paths)
    backbone = resolve_backbone(df, args.backbone, cfg)
    if "backbone" in df.columns:
        df = df[df["backbone"] == backbone]
        if df.empty:
            raise SystemExit(f"no rows for backbone {backbone!r}")

    split_ids: Dict[str, List[str]] = {}
    if args.split != "all":
        ratios = tuple(float(part) for part in args.split_ratios.split(","))
        split_ids = participant_split(df["participant_id"].unique(), ratios, args.split_seed)
        df = df[df["participant_id"].isin(split_ids[args.split])]
        if df.empty:
            raise SystemExit(f"split {args.split!r} is empty for these participants")
    else:
        split_ids = {"all": sorted({str(pid) for pid in df["participant_id"].dropna().unique()})}

    arms = [run_arm(df, cfg, key, smooth=args.temporal) for key in feature_sets]
    rows = [arm["row"] for arm in arms]
    selection = select_feature_set(rows, cfg.release_gate)
    matrix = per_user_matrix(arms)

    timestamp = args.timestamp or now_utc_iso()
    run_name = args.run_name or (
        f"exp2_calibration_ablation_{timestamp.replace(':', '').replace('-', '')[:15]}"
    )
    out_dir = Path(args.out_dir) if args.out_dir else REPORTS_DIR / run_name
    fingerprint = dataset_fingerprint(df)

    report: Dict[str, Any] = {
        "run": {
            "experiment": EXPERIMENT,
            "name": run_name,
            "timestamp_utc": timestamp,
            "tables": [str(path) for path in paths],
            "feature_sets": feature_sets,
            "backbone": backbone,
            "split": args.split,
            "split_participants": split_ids,
            "thresholds": {
                "p_max_threshold": cfg.calibration.p_max_threshold,
                "margin_threshold": cfg.calibration.margin_threshold,
            },
            "temporal": bool(args.temporal),
            "configured_feature_set": normalise_feature_set(cfg.calibration.feature_set),
            "code_commit": git_commit(),
        },
        "dataset": fingerprint,
        "arms": [{key: value for key, value in arm.items() if key != "frames"} for arm in arms],
        "selection": selection,
        "verdict": verdict_lines(
            selection, rows, normalise_feature_set(cfg.calibration.feature_set)
        ),
        "per_user_matrix": matrix.to_dict(orient="records"),
        "per_user_matrix_columns": list(matrix.columns),
    }

    written = write_report(report, out_dir, run_name, arms=arms, dump_frames=args.dump_frames)

    if not args.no_log:
        records = [
            ExperimentRecord(
                experiment=EXPERIMENT,
                variant=f"set_{arm['feature_set']}",
                experiment_id=make_experiment_id(
                    EXPERIMENT, timestamp, arm["config_hash"], f"set_{arm['feature_set']}"
                ),
                timestamp_utc=timestamp,
                code_commit=report["run"]["code_commit"],
                config_hash=arm["config_hash"],
                dataset_version=fingerprint["version"],
                model_version=f"{MODEL_VERSION}/{backbone}/feature_set:{arm['feature_set']}",
                backbone=backbone,
                feature_set=arm["feature_set"],
                thresholds=report["run"]["thresholds"],
                participant_split=split_ids,
                metrics={
                    **{
                        key: arm["row"][key]
                        for key in (
                            "macro_f1",
                            "bottom_recall",
                            "uncertain_ratio",
                            "per_user_f1_mean",
                            "per_user_f1_min",
                            "per_user_f1_std",
                            "calibration_failure_rate",
                            "n_calibration_failed",
                            "loo_accuracy_mean",
                            "separability_mean",
                            "n_features",
                        )
                    },
                    "gate_passed": arm["row"]["gate_passed"],
                    "calibration_fail_reasons": arm["calibration_reasons"],
                    "recommended": selection["recommended"] == arm["feature_set"],
                },
                latency=arm["latency"],
                failure_cases=failure_case_links(arm, written["markdown"]),
                artefacts={key: str(path) for key, path in written.items()},
                notes=(
                    f"doc 23 Experiment 2 arm; features={', '.join(arm['feature_names'])}; "
                    f"temporal={'on' if args.temporal else 'off'}"
                ),
            )
            for arm in arms
        ]
        log_paths = update_log(records, args.log, generated_utc=timestamp)
        written.update({f"log_{key}": path for key, path in log_paths.items()})

    print(
        f"exp2: {len(arms)} feature-set arm(s) over {fingerprint['n_rows']} rows "
        f"({fingerprint['n_participants']} participants, backbone={backbone}, "
        f"split={args.split})"
    )
    print(
        pd.DataFrame(rows)[
            [
                "feature_set",
                "n_features",
                "macro_f1",
                "per_user_f1_mean",
                "per_user_f1_min",
                "calibration_failure_rate",
                "gate_passed",
            ]
        ].to_string(index=False)
    )
    for line in report["verdict"]:
        print(f"  - {line}")
    for name, path in written.items():
        print(f"  {name}: {path}")
    return 0 if selection["recommended"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
