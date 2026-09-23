"""Sweep the doc 5-4 UNCERTAIN thresholds on the validation split (doc 7, doc 19).

``p_max_threshold`` and ``margin_threshold`` decide when the classifier abstains,
and they trade the two things doc 7 gates against each other: raising them buys
accuracy on the frames that are still decided and spends it on
``uncertain_ratio``.  There is no way to pick them from first principles, so
they are swept -- on the **validation** split only, because a threshold chosen
on test is a test-set fit.

The sweep is cheap because the thresholds are applied *after* the model: each
participant's classifier is fitted once, the probabilities are cached, and each
grid point is one vectorised pass of ``gaze_eval.apply_uncertain_rule`` over
those cached numbers.  Fitting inside the grid would give identical results and
cost the grid size in LR fits.

Selection rule (stated because a single "best" number always hides one): the
chosen point maximises macro F1 **subject to** the doc 7 gate rows this sweep
can move -- ``uncertain_ratio <= max_uncertain_ratio`` and
``bottom_recall >= min_bottom_recall``.  Ties break toward the point that
abstains least, then toward the lowest thresholds.  The best unconstrained
point is reported next to it, so a config that only wins by abstaining is
visible.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
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

from vision.config import REPORTS_DIR, VisionConfig, load_config  # noqa: E402
from vision.data.splits import participant_split, per_participant_partition  # noqa: E402

from evaluation.gaze_eval import (  # noqa: E402
    jsonable,
    md_table,
    apply_uncertain_rule,
    load_tables,
    resolve_tables,
    score_participant,
)
from evaluation.metrics import evaluate_release_gate, frame_metrics, per_user_metrics  # noqa: E402

#: Grid defaults.  ``p_max`` starts at 0.50 because below it the rule cannot
#: bind (two classes, ``p_max >= 0.5`` always), and ``margin`` starts at 0.0 =
#: "margin rule off".  Note for the reader of a sweep plot: for a two-class
#: model ``margin == 2 * p_max - 1``, so the two axes are not independent --
#: the margin rule only bites where ``margin_threshold > 2 * p_max_threshold - 1``
#: (the upper-left region of the grid).
DEFAULT_P_MAX_GRID: Tuple[float, ...] = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
DEFAULT_MARGIN_GRID: Tuple[float, ...] = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def cached_probabilities(df: pd.DataFrame, cfg: VisionConfig) -> pd.DataFrame:
    """Fit each participant once and return their evaluation frames with p(class).

    The returned frame carries ``label``, ``p_camera``, ``p_bottom`` and
    ``decision_face_valid`` -- everything a threshold needs and nothing it does
    not.  Participants whose calibration could not be fitted at all are dropped
    here and reported by the caller: with no probabilities they would sit at
    100 % UNCERTAIN in every cell and flatten the whole grid.
    """
    partition = per_participant_partition(df, cfg)
    frames: List[pd.DataFrame] = []
    unfitted: List[str] = []
    failed_calibration: List[str] = []
    for participant_id, parts in partition.items():
        if parts["evaluation"].empty:
            continue
        result = score_participant(
            participant_id, parts["calibration"], parts["evaluation"], cfg, smooth=False
        )
        if not result.fitted:
            unfitted.append(participant_id)
            continue
        if result.calibration_failed:
            failed_calibration.append(participant_id)
        frames.append(result.rows)
    if not frames:
        raise ValueError("no participant produced a fitted classifier; cannot sweep thresholds")
    out = pd.concat(frames, ignore_index=True)
    out.attrs["unfitted_participants"] = unfitted
    out.attrs["failed_calibration_participants"] = failed_calibration
    return out


def sweep(
    scored: pd.DataFrame,
    p_max_grid: Sequence[float],
    margin_grid: Sequence[float],
) -> pd.DataFrame:
    """One row per grid point: the doc 7 metrics under that decision rule."""
    rows: List[Dict[str, Any]] = []
    for p_max_threshold in p_max_grid:
        for margin_threshold in margin_grid:
            predictions = apply_uncertain_rule(
                scored["p_camera"],
                scored["p_bottom"],
                scored["decision_face_valid"],
                p_max_threshold,
                margin_threshold,
            )
            working = scored.assign(pred_label=predictions)
            scores = frame_metrics(working["label"], working["pred_label"])
            per_user = per_user_metrics(working)
            summary = per_user.attrs["summary"]
            rows.append(
                {
                    "p_max_threshold": float(p_max_threshold),
                    "margin_threshold": float(margin_threshold),
                    "macro_f1": scores["macro_f1"],
                    "bottom_recall": scores["bottom_recall"],
                    "camera_recall": scores["camera_recall"],
                    "accuracy": scores["accuracy"],
                    "uncertain_ratio": scores["uncertain_ratio"],
                    "n_decided": scores["n_decided"],
                    "macro_f1_uncertain_as_error": scores["macro_f1_uncertain_as_error"],
                    "per_user_f1_min": summary.get("macro_f1_min"),
                    "per_user_f1_mean": summary.get("macro_f1_mean"),
                }
            )
    return pd.DataFrame(rows)


def select_best(grid: pd.DataFrame, cfg: VisionConfig) -> Dict[str, Any]:
    """Apply the selection rule stated in the module docstring."""
    gate = cfg.release_gate
    feasible = grid[
        (grid["uncertain_ratio"] <= float(gate.max_uncertain_ratio))
        & (grid["bottom_recall"] >= float(gate.min_bottom_recall))
    ]
    # Ties are common and not cosmetic: for two classes p_max and margin encode
    # the same cut (margin == 2*p_max - 1), so whole diagonals of the grid are
    # the same rule.  Break by fewest abstentions first, then by the lowest
    # thresholds, so the reported point is reproducible rather than whichever
    # row the sort happened to see first.
    order = ["macro_f1", "uncertain_ratio", "p_max_threshold", "margin_threshold"]
    ascending = [False, True, True, True]
    unconstrained = grid.sort_values(order, ascending=ascending, kind="stable")
    best_unconstrained = unconstrained.iloc[0].to_dict() if len(unconstrained) else None
    best_feasible = None
    if len(feasible):
        best_feasible = feasible.sort_values(order, ascending=ascending, kind="stable").iloc[0].to_dict()
    return {
        "constraints": {
            "max_uncertain_ratio": float(gate.max_uncertain_ratio),
            "min_bottom_recall": float(gate.min_bottom_recall),
        },
        "n_feasible": int(len(feasible)),
        "best": best_feasible or best_unconstrained,
        "best_is_feasible": best_feasible is not None,
        "best_feasible": best_feasible,
        "best_unconstrained": best_unconstrained,
        "current": {
            "p_max_threshold": float(cfg.calibration.p_max_threshold),
            "margin_threshold": float(cfg.calibration.margin_threshold),
        },
    }


def current_point(grid: pd.DataFrame, cfg: VisionConfig) -> Optional[Dict[str, Any]]:
    """The grid row matching the configured thresholds, when it is on the grid."""
    match = grid[
        np.isclose(grid["p_max_threshold"], float(cfg.calibration.p_max_threshold))
        & np.isclose(grid["margin_threshold"], float(cfg.calibration.margin_threshold))
    ]
    return match.iloc[0].to_dict() if len(match) else None


# --------------------------------------------------------------------------
# Plot
# --------------------------------------------------------------------------


def plot_sweep(grid: pd.DataFrame, best: Mapping[str, Any], path: Path) -> Optional[Path]:
    """Two heatmaps -- macro F1 and UNCERTAIN ratio -- over the same grid.

    Both are needed side by side: the F1 surface alone always argues for the
    highest threshold, and only the abstention surface shows what that costs.
    Returns ``None`` when matplotlib is unavailable, which is not fatal -- the
    CSV and the JSON carry the same numbers.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")  # a report run has no display; fixed before pyplot
        import matplotlib.pyplot as plt
    except Exception:
        return None

    p_values = sorted(grid["p_max_threshold"].unique())
    m_values = sorted(grid["margin_threshold"].unique())
    figure, axes = plt.subplots(1, 2, figsize=(12, 4.5), constrained_layout=True)
    for axis, column, title, cmap in (
        (axes[0], "macro_f1", "macro F1 (decided frames)", "viridis"),
        (axes[1], "uncertain_ratio", "UNCERTAIN ratio", "magma_r"),
    ):
        matrix = (
            grid.pivot(index="p_max_threshold", columns="margin_threshold", values=column)
            .reindex(index=p_values, columns=m_values)
            .to_numpy(dtype=float)
        )
        image = axis.imshow(matrix, origin="lower", aspect="auto", cmap=cmap)
        axis.set_xticks(range(len(m_values)), [f"{v:g}" for v in m_values])
        axis.set_yticks(range(len(p_values)), [f"{v:g}" for v in p_values])
        axis.set_xlabel("margin_threshold")
        axis.set_ylabel("p_max_threshold")
        axis.set_title(title)
        figure.colorbar(image, ax=axis)
        if best:
            axis.plot(
                m_values.index(float(best["margin_threshold"])),
                p_values.index(float(best["p_max_threshold"])),
                marker="*",
                markersize=16,
                color="white",
                markeredgecolor="black",
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=120)
    plt.close(figure)
    return path


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def render_markdown(report: Mapping[str, Any], run_name: str) -> str:
    selection = report["selection"]
    best = selection["best"]
    lines = [
        f"# threshold_sweep - {run_name}",
        "",
        f"- split: `{report['run']['split']}`  frames: {report['dataset']['n_frames']}  "
        f"participants: {report['dataset']['n_participants']}",
        f"- grid: {len(report['grid'])} points "
        f"(p_max {report['run']['p_max_grid']}, margin {report['run']['margin_grid']})",
        "",
        "## Selected thresholds",
        "",
        f"Rule: maximise macro F1 subject to uncertain_ratio <= "
        f"{selection['constraints']['max_uncertain_ratio']} and bottom_recall >= "
        f"{selection['constraints']['min_bottom_recall']} "
        f"({selection['n_feasible']} of {len(report['grid'])} points feasible); "
        "ties go to the point that abstains least, then to the lowest thresholds.",
        "",
        md_table(
            [
                {"point": "current config", **(report["current_point"] or {})},
                {"point": "best feasible", **(selection["best_feasible"] or {})},
                {"point": "best unconstrained", **(selection["best_unconstrained"] or {})},
            ],
            [
                "point",
                "p_max_threshold",
                "margin_threshold",
                "macro_f1",
                "bottom_recall",
                "uncertain_ratio",
                "per_user_f1_min",
            ],
        ),
        "```yaml",
        "# ai/configs/calibration.yaml",
        f"p_max_threshold: {best['p_max_threshold'] if best else '-'}",
        f"margin_threshold: {best['margin_threshold'] if best else '-'}",
        "```",
        "",
        "## Release gate at the selected point (doc 7)",
        "",
        md_table(
            report["release_gate_at_best"]["rows"],
            ["metric", "value", "target", "direction", "pass"],
        ),
        "Latency and calibration-failure rows are not measured by a sweep and "
        "therefore read as failures here; take them from `gaze_eval` on the same run.",
        "",
        "## Grid (top 15 by macro F1)",
        "",
        md_table(
            sorted(report["grid"], key=lambda row: -row["macro_f1"])[:15],
            [
                "p_max_threshold",
                "margin_threshold",
                "macro_f1",
                "bottom_recall",
                "uncertain_ratio",
                "n_decided",
                "per_user_f1_min",
            ],
        ),
    ]
    if report.get("plot"):
        lines += ["", f"![sweep]({Path(report['plot']).name})", ""]
    return "\n".join(lines) + "\n"


def write_report(report: Mapping[str, Any], out_dir: Path, run_name: str) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "threshold_sweep.csv"
    pd.DataFrame(report["grid"]).to_csv(csv_path, index=False)
    json_path = out_dir / "threshold_sweep.json"
    json_path.write_text(json.dumps(jsonable(report), indent=2), encoding="utf-8")
    md_path = out_dir / "threshold_sweep.md"
    md_path.write_text(render_markdown(report, run_name), encoding="utf-8")
    written = {"csv": csv_path, "json": json_path, "markdown": md_path}
    if report.get("plot"):
        written["plot"] = Path(report["plot"])
    return written


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _parse_grid(text: str, fallback: Sequence[float]) -> Tuple[float, ...]:
    """Accept ``a,b,c`` or ``start:stop:step`` (stop inclusive)."""
    if not text:
        return tuple(fallback)
    if ":" in text:
        start, stop, step = (float(part) for part in text.split(":"))
        if step <= 0:
            raise ValueError(f"step must be > 0 in {text!r}")
        count = int(math.floor((stop - start) / step + 1e-9)) + 1
        return tuple(round(start + index * step, 6) for index in range(max(count, 1)))
    return tuple(float(part) for part in text.split(",") if part.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="threshold_sweep",
        description=(
            "Sweep the doc 5-4 p_max / margin thresholds on the validation split and write "
            "ai/reports/<run>/threshold_sweep.{csv,json,md,png}."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--table", action="append", default=[], help="feature table; repeatable")
    parser.add_argument("--features-dir", default=None, help="directory of feature tables")
    parser.add_argument("--config-dir", default=None, help="override ai/configs")
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument(
        "--split",
        choices=("all", "train", "val", "test"),
        default="val",
        help="doc 4-3 participant split to sweep on; test is deliberately not the default",
    )
    parser.add_argument("--split-seed", type=int, default=42)
    parser.add_argument("--split-ratios", default="0.6,0.2,0.2")
    parser.add_argument(
        "--p-max-grid",
        default="",
        help="comma list or start:stop:step (default: 0.50..0.90 step 0.05)",
    )
    parser.add_argument(
        "--margin-grid", default="", help="comma list or start:stop:step (default: 0..0.6)"
    )
    parser.add_argument("--no-plot", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(Path(args.config_dir) if args.config_dir else None)
    p_max_grid = _parse_grid(args.p_max_grid, DEFAULT_P_MAX_GRID)
    margin_grid = _parse_grid(args.margin_grid, DEFAULT_MARGIN_GRID)

    paths = resolve_tables(args.table, args.features_dir)
    df = load_tables(paths)
    if args.split != "all":
        ratios = tuple(float(part) for part in args.split_ratios.split(","))
        splits = participant_split(df["participant_id"].unique(), ratios, args.split_seed)
        df = df[df["participant_id"].isin(splits[args.split])]
        if df.empty:
            raise SystemExit(
                f"split {args.split!r} is empty; with few participants use --split all"
            )

    scored = cached_probabilities(df, cfg)
    grid = sweep(scored, p_max_grid, margin_grid)
    selection = select_best(grid, cfg)

    run_name = args.run_name or datetime.now(timezone.utc).strftime("sweep_%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else REPORTS_DIR / run_name

    plot_path = None
    if not args.no_plot:
        plot_path = plot_sweep(grid, selection["best"] or {}, out_dir / "threshold_sweep.png")

    best = selection["best"] or {}
    report: Dict[str, Any] = {
        "run": {
            "name": run_name,
            "tables": [str(path) for path in paths],
            "split": args.split,
            "p_max_grid": list(p_max_grid),
            "margin_grid": list(margin_grid),
            "config_hash": cfg.hash(),
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "dataset": {
            "n_frames": int(len(scored)),
            "n_participants": int(scored["participant_id"].nunique()),
            "unfitted_participants": scored.attrs.get("unfitted_participants", []),
            "failed_calibration_participants": scored.attrs.get(
                "failed_calibration_participants", []
            ),
        },
        "grid": grid.to_dict(orient="records"),
        "selection": selection,
        "current_point": current_point(grid, cfg),
        # Only the rows a threshold can move are meaningful here; the others are
        # kept so the shape matches gaze_eval's gate and the gaps are visible.
        "release_gate_at_best": evaluate_release_gate(
            {
                "macro_f1": best.get("macro_f1"),
                "bottom_recall": best.get("bottom_recall"),
                "uncertain_ratio": best.get("uncertain_ratio"),
                "per_user_f1_min": best.get("per_user_f1_min"),
            },
            cfg.release_gate,
        ),
        "plot": str(plot_path) if plot_path else None,
    }
    written = write_report(report, out_dir, run_name)

    print(
        f"swept {len(grid)} points over {report['dataset']['n_frames']} frames "
        f"({report['dataset']['n_participants']} participants, split={args.split})"
    )
    if best:
        print(
            f"best: p_max={best['p_max_threshold']:.2f} margin={best['margin_threshold']:.2f} "
            f"macro_f1={best['macro_f1']:.4f} bottom_recall={best['bottom_recall']:.4f} "
            f"uncertain_ratio={best['uncertain_ratio']:.4f} "
            f"feasible={selection['best_is_feasible']}"
        )
    for name, path in written.items():
        print(f"  {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
