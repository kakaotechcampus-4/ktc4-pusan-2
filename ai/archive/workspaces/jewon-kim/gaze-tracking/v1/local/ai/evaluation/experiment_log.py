"""Append-only experiment record (doc 18).

doc 18 asks that an experiment be reconstructible from its record alone: what
was run, on which data, with which code and which settings, and what came out.
One JSONL line is one *arm* -- one backbone in doc 23's Experiment 1, one
feature set in Experiment 2 -- not one experiment.  Both experiments exist to
compare arms, and a row that averaged the arms together would answer none of
the questions the comparison was run to ask.

The JSONL file is append-only and is the source of truth.  The markdown table
beside it is a *view*: it is re-rendered from the whole file after every
append, so a hand edit to the markdown is dropped on the next run instead of
quietly disagreeing with the log.  Because the JSONL is never rewritten, a
record written by a future schema loses nothing when an older build reads it --
:meth:`ExperimentRecord.from_dict` drops fields it does not know, but only in
the rendered view.

Timestamps are arguments here, never ``datetime.now()`` calls inside a library
function.  Re-rendering a log from March must not stamp today's date onto its
rows, and two records written from one run must agree in that field.
:func:`now_utc_iso` exists for the CLI at the edge to call once.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

import pandas as pd


def _bootstrap_import_path() -> None:
    """See ``metrics._bootstrap_import_path``; repeated so any entry point works."""
    root = Path(__file__).resolve().parents[2]
    for entry in (root / "ai" / "src", root / "ai"):
        text = str(entry)
        if text not in sys.path:
            sys.path.insert(0, text)


_bootstrap_import_path()

from vision.config import REPORTS_DIR  # noqa: E402

from evaluation.gaze_eval import md_table  # noqa: E402

PathLike = Union[str, Path]

#: Bumped when :class:`ExperimentRecord` changes shape.  Unlike the calibration
#: model's version this one is *not* enforced on read: a log outlives a schema
#: bump, and refusing to read last month's rows would destroy the history the
#: log exists for.  The column is rendered instead, so a mixed file is visible.
SCHEMA_VERSION = "experiment_log_v1"

DEFAULT_LOG_PATH = REPORTS_DIR / "experiment_log.jsonl"
DEFAULT_MARKDOWN_PATH = REPORTS_DIR / "experiment_log.md"

#: Flattened columns of the markdown view, in the order doc 18 lists the fields:
#: identity, provenance, settings, then results.
LOG_TABLE_COLUMNS: Sequence[str] = (
    "experiment_id",
    "timestamp_utc",
    "experiment",
    "variant",
    "backbone",
    "feature_set",
    "p_max_threshold",
    "margin_threshold",
    "dataset_version",
    "model_version",
    "code_commit",
    "config_hash",
    "split",
    "macro_f1",
    "bottom_recall",
    "per_user_f1_mean",
    "per_user_f1_min",
    "uncertain_ratio",
    "calibration_failure_rate",
    "p50_ms",
    "p95_ms",
    "gate_passed",
    "n_failure_cases",
)


@dataclass
class ExperimentRecord:
    """One arm of one experiment, carrying every doc 18 field.

    ``metrics`` and ``latency`` are free-form dicts rather than fixed columns
    because the two experiments report different things (Experiment 1 adds a
    glasses subgroup delta and a model size, Experiment 2 adds a calibration
    failure rate per feature set).  :data:`LOG_TABLE_COLUMNS` pulls the shared
    subset out for the markdown view; the JSON keeps everything.
    """

    experiment: str
    variant: str
    experiment_id: str
    timestamp_utc: str
    code_commit: str
    config_hash: str
    dataset_version: str
    model_version: str
    backbone: str = "unknown"
    feature_set: str = "C"
    thresholds: Dict[str, Any] = field(default_factory=dict)
    participant_split: Dict[str, List[str]] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    latency: Dict[str, Any] = field(default_factory=dict)
    #: doc 18 "failure-case links": ``{"label": ..., "ref": ...}`` pointers to
    #: where the failures of this arm can be looked at (a bucket row, a report).
    failure_cases: List[Dict[str, str]] = field(default_factory=list)
    #: Files this arm wrote, by kind -- the reader's way back to the numbers.
    artefacts: Dict[str, str] = field(default_factory=dict)
    notes: str = ""
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExperimentRecord":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def now_utc_iso() -> str:
    """UTC timestamp for one CLI invocation to stamp its records with.

    The only clock read in this module, and nothing in this module calls it:
    every function takes the timestamp as an argument so that a record is a
    pure function of its inputs (module docstring).
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_experiment_id(
    experiment: str, timestamp_utc: str, config_hash: str, variant: str = ""
) -> str:
    """Deterministic id: ``exp1-backbone-l2cs-20260906T101112-ab12cd34``.

    Derived from its inputs rather than from a counter or a UUID so that a
    re-run of the same arm at the same recorded timestamp produces the same id
    -- doc 18 identifies an experiment by what it was, not by when the line
    happened to be appended.  The timestamp is compacted to
    ``YYYYMMDDThhmmss``; the offset is dropped because it is always UTC.
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{experiment}-{variant}").strip("-").lower()
    stamp = re.sub(r"[^0-9T]", "", str(timestamp_utc))[:15]
    return f"{slug}-{stamp}-{str(config_hash)[:8]}"


def dataset_fingerprint(df: pd.DataFrame) -> Dict[str, Any]:
    """Content identity of the evaluated feature table (doc 18 dataset version).

    A file path is not a dataset version: ``extract_features`` overwrites its
    output in place, so two runs can name the same file and mean different
    frames.  The digest is taken over the sorted ``sample_id`` values and the
    label versions present, which is what changes when the data changes and is
    stable against row order, added columns and the parquet/CSV storage
    fallback.  It deliberately ignores the measured columns: re-running the
    same protocol through a new backbone is the same dataset, and Experiment 1
    compares two backbones over one.
    """
    if "sample_id" in df.columns:
        keys = sorted(str(value) for value in df["sample_id"].dropna().tolist())
    else:
        # A table written without ids still deserves a version; fall back to the
        # coordinates doc 17 builds sample_id from.
        columns = [c for c in ("participant_id", "session_id", "frame_id") if c in df.columns]
        keys = sorted(
            "|".join(str(value) for value in row) for row in df[columns].itertuples(index=False)
        )

    label_versions = (
        sorted({str(v) for v in df["label_version"].dropna().unique()})
        if "label_version" in df.columns
        else []
    )
    digest = hashlib.sha256(
        ("\n".join(keys) + "\n#" + ",".join(label_versions)).encode("utf-8")
    ).hexdigest()

    participants = (
        sorted({str(v) for v in df["participant_id"].dropna().unique()})
        if "participant_id" in df.columns
        else []
    )
    backbones = (
        sorted({str(v) for v in df["backbone"].dropna().unique()})
        if "backbone" in df.columns
        else []
    )
    label_part = "+".join(label_versions) if label_versions else "unlabelled"
    return {
        "version": f"{label_part}:{len(df)}f:{len(participants)}p:{digest[:12]}",
        "digest": digest,
        "n_rows": int(len(df)),
        "n_participants": len(participants),
        "participants": participants,
        "label_versions": label_versions,
        "backbones": backbones,
    }


def dataset_version(df: pd.DataFrame) -> str:
    """Just the version string of :func:`dataset_fingerprint`."""
    return str(dataset_fingerprint(df)["version"])


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def append_records(
    records: Sequence[ExperimentRecord], path: Optional[PathLike] = None
) -> Path:
    """Append one JSON line per record and return the log path.

    Opened with an explicit newline so Windows does not turn every line ending
    into CRLF: a log copied to another machine would otherwise read back with a
    carriage return inside its last JSON value.
    """
    target = Path(path) if path is not None else DEFAULT_LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record.to_dict(), sort_keys=False) + "\n")
    return target


def read_records(path: Optional[PathLike] = None) -> List[ExperimentRecord]:
    """Read every record in the log, oldest first.

    A malformed line raises with its line number rather than being skipped: a
    truncated write is a real problem and a silently shorter table would hide
    it.  A *foreign schema version* is not malformed and is kept (see
    :data:`SCHEMA_VERSION`).
    """
    target = Path(path) if path is not None else DEFAULT_LOG_PATH
    if not target.exists():
        return []
    records: List[ExperimentRecord] = []
    with target.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{target}:{number}: not valid JSON ({exc})") from None
            if not isinstance(payload, dict):
                raise ValueError(f"{target}:{number}: expected a JSON object")
            records.append(ExperimentRecord.from_dict(payload))
    return records


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


def _split_summary(split: Mapping[str, Any]) -> str:
    """``train=4/val=1/test=1`` -- sizes only; the ids are in the JSON."""
    if not split:
        return "-"
    return "/".join(f"{name}={len(members)}" for name, members in split.items())


def flatten_record(record: ExperimentRecord) -> Dict[str, Any]:
    """One flat row for the markdown view and for :func:`records_to_frame`."""
    metrics = record.metrics or {}
    latency = record.latency or {}
    thresholds = record.thresholds or {}
    return {
        "experiment_id": record.experiment_id,
        "timestamp_utc": record.timestamp_utc,
        "experiment": record.experiment,
        "variant": record.variant,
        "backbone": record.backbone,
        "feature_set": record.feature_set,
        "p_max_threshold": thresholds.get("p_max_threshold"),
        "margin_threshold": thresholds.get("margin_threshold"),
        "dataset_version": record.dataset_version,
        "model_version": record.model_version,
        "code_commit": record.code_commit,
        "config_hash": record.config_hash,
        "split": _split_summary(record.participant_split),
        "macro_f1": metrics.get("macro_f1"),
        "bottom_recall": metrics.get("bottom_recall"),
        "per_user_f1_mean": metrics.get("per_user_f1_mean"),
        "per_user_f1_min": metrics.get("per_user_f1_min"),
        "uncertain_ratio": metrics.get("uncertain_ratio"),
        "calibration_failure_rate": metrics.get("calibration_failure_rate"),
        "p50_ms": latency.get("p50"),
        "p95_ms": latency.get("p95"),
        "gate_passed": metrics.get("gate_passed"),
        "n_failure_cases": len(record.failure_cases or []),
        "schema_version": record.schema_version,
    }


def records_to_frame(records: Sequence[ExperimentRecord]) -> pd.DataFrame:
    """The log as a frame, one row per arm, in file order."""
    return pd.DataFrame(
        [flatten_record(record) for record in records],
        columns=list(LOG_TABLE_COLUMNS) + ["schema_version"],
    )


def render_markdown(
    records: Sequence[ExperimentRecord],
    *,
    title: str = "Experiment log (doc 18)",
    generated_utc: Optional[str] = None,
) -> str:
    """Render the whole log as one markdown table plus the failure-case links.

    ``generated_utc`` is an argument and may be ``None``: this function has to
    be able to reproduce an old file exactly (module docstring).
    """
    rows = [flatten_record(record) for record in records]
    lines: List[str] = [f"# {title}", ""]
    if generated_utc:
        lines.append(f"_rendered {generated_utc} from {len(records)} record(s)._")
    else:
        lines.append(f"_{len(records)} record(s)._")
    lines += [
        "",
        "One row per experiment arm. The JSONL beside this file is the source of "
        "truth; this table is regenerated from it and hand edits are lost.",
        "",
        md_table(rows, list(LOG_TABLE_COLUMNS)),
    ]

    links = [
        (record, case) for record in records for case in (record.failure_cases or [])
    ]
    if links:
        lines += [
            "",
            "## Failure-case links",
            "",
            md_table(
                [
                    {
                        "experiment_id": record.experiment_id,
                        "label": case.get("label", ""),
                        "ref": case.get("ref", ""),
                    }
                    for record, case in links
                ],
                ["experiment_id", "label", "ref"],
            ),
        ]
    return "\n".join(lines) + "\n"


def write_markdown(
    records: Sequence[ExperimentRecord],
    path: Optional[PathLike] = None,
    *,
    generated_utc: Optional[str] = None,
) -> Path:
    """Write the markdown view; returns the path."""
    target = Path(path) if path is not None else DEFAULT_MARKDOWN_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(records, generated_utc=generated_utc), encoding="utf-8")
    return target


def _markdown_beside(log_path: Path) -> Path:
    """``<log>.jsonl`` -> ``<log>.md``, so a custom log path keeps its view."""
    return log_path.with_suffix(".md")


def update_log(
    records: Sequence[ExperimentRecord],
    log_path: Optional[PathLike] = None,
    markdown_path: Optional[PathLike] = None,
    *,
    generated_utc: Optional[str] = None,
) -> Dict[str, Path]:
    """Append records, then re-render the markdown view from the whole file.

    Re-reading the file instead of rendering only the records just appended is
    what makes the markdown a view of the log rather than of the last run: two
    experiments run an hour apart both appear in the table.
    """
    written = append_records(records, log_path)
    markdown = write_markdown(
        read_records(written),
        markdown_path if markdown_path is not None else _markdown_beside(written),
        generated_utc=generated_utc,
    )
    return {"jsonl": written, "markdown": markdown}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="experiment_log",
        description=(
            "Re-render the doc 18 experiment log markdown from its JSONL and print the most "
            "recent arms. Experiments append to the log themselves; this only reads."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--log", default=str(DEFAULT_LOG_PATH), help="JSONL log to read")
    parser.add_argument(
        "--markdown", default=None, help="markdown view to write (default: <log>.md)"
    )
    parser.add_argument("--tail", type=int, default=10, help="rows to print")
    parser.add_argument(
        "--no-render", action="store_true", help="print only, do not rewrite the markdown"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    log_path = Path(args.log)
    records = read_records(log_path)
    if not records:
        print(f"no experiment records in {log_path}")
        return 0

    if not args.no_render:
        markdown = write_markdown(
            records,
            Path(args.markdown) if args.markdown else _markdown_beside(log_path),
            generated_utc=now_utc_iso(),
        )
        print(f"rendered {len(records)} record(s) -> {markdown}")

    frame = records_to_frame(records)
    columns = [
        "experiment_id",
        "variant",
        "macro_f1",
        "bottom_recall",
        "per_user_f1_min",
        "p95_ms",
        "gate_passed",
    ]
    with pd.option_context("display.width", 200, "display.max_columns", 50):
        print(frame[columns].tail(max(0, int(args.tail))).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
