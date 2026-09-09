"""Recorded take + segment labels -> the offline evaluation table (doc 20).

doc 20 keeps no pixels for evaluation, so this is the step that turns a video
into numbers and then never needs the video again: every experiment (doc 23),
every failure bucket (doc 19) and the release gate (doc 7) run off the table
this writes.

Sample once, run every backbone on the same frames
--------------------------------------------------
``--backbone`` is repeatable and produces one table per backbone from a *single*
pass of preprocess.  That is not an optimisation, it is what makes doc 23's
Experiment 1 a comparison: two runs would decimate to two different frame sets
(``FrameSampler`` is timestamp-driven and a re-decode can land a frame either
side of a deadline), so the backbones would be scored on different data and the
difference would partly be sampling.  Here the landmarks, crops, head pose and
quality signals are computed once and shared, and the only thing that varies
between the tables is the gaze estimate.

What is deliberately not filled in
----------------------------------
``pred_label`` / ``p_camera`` / ``p_bottom`` / ``latency_ms`` stay empty.  There
is no classifier at extraction time -- doc 5-3's model is per user and is fitted
from that user's own calibration block, which ``gaze_eval`` does under the doc
4-3 protocol.  Writing a decision here would either need a second, differently
fitted classifier or a fabricated probability, and ``gaze_eval._resolve_latency``
already knows how to sum the stages that *were* measured and label the result
``sum_of_stages``.

Run it::

    ./.venv/Scripts/python.exe -m ai.tools.extract_features \\
        --video ai/datasets/raw/P01/S01.mp4 --backbone mediapipe_geom
    ./.venv/Scripts/python.exe -m ai.tools.extract_features --video ... --dry-run
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2

# Allow running as a plain script as well as with -m.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "ai" / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from vision.backbones.base import GazeBackbone  # noqa: E402
from vision.backbones.registry import available_backbones, build_backbone  # noqa: E402
from vision.config import DATASETS_DIR, REPO_ROOT, VisionConfig, load_config  # noqa: E402
from vision.data.features_table import (  # noqa: E402
    observations_to_rows,
    resolve_table_path,
    rows_to_frame,
    save_table,
)
from vision.data.labels import load_segments, segment_summary  # noqa: E402
from vision.data.manifest import make_sample_id, write_manifest  # noqa: E402
from vision.preprocess.pipeline import PreprocessPipeline  # noqa: E402
from vision.preprocess.sampler import iter_video_frames  # noqa: E402
from vision.schemas import (  # noqa: E402
    FrameObservation,
    GazeLabel,
    GazeVector,
    ManifestRecord,
    ParticipantMeta,
    SegmentLabel,
)

#: Label-file name written by ``ai/tools/collect.py`` and ``ai/tools/label_review.py``.
PROTOCOL_LABELS = "{pid}_{sid}.segments.json"
CORRECTED_LABELS = "{pid}_{sid}.segments.corrected.json"


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass
class Source:
    """One recorded take plus everything needed to label and attribute it."""

    video: Path
    meta: ParticipantMeta
    session_id: str
    #: Session-level lighting from the collector, used only where a segment
    #: carries none of its own.
    lighting: str
    #: Set only by ``--lighting``; an explicit override beats the label file.
    lighting_override: Optional[str]
    segments: List[SegmentLabel]
    labels_path: Optional[Path]

    @property
    def participant_id(self) -> str:
        return self.meta.participant_id


class SegmentIndex:
    """``t_ms`` -> the segment covering it, or ``None`` (doc 4-2 half-open).

    Bisect rather than a scan: a 5-minute alternating take carries ~120
    segments and ~2400 sampled frames, and the scan is the only per-frame cost
    here that grows with both.
    """

    def __init__(self, segments: Sequence[SegmentLabel]) -> None:
        self.segments = sorted(segments, key=lambda s: (s.start_ms, s.end_ms))
        self.starts = [s.start_ms for s in self.segments]

    def at(self, t_ms: int) -> Optional[SegmentLabel]:
        position = bisect.bisect_right(self.starts, int(t_ms)) - 1
        if position < 0:
            return None
        segment = self.segments[position]
        return segment if segment.contains(int(t_ms)) else None


def discover_labels(labels_dir: Path, pid: str, sid: str, *, prefer_protocol: bool) -> Optional[Path]:
    """Find the label file for a take, preferring the reviewed one.

    ``label_review`` writes its corrections beside the untouched protocol file,
    so the corrected copy is the better ground truth wherever it exists;
    ``--protocol-labels`` forces the original for a run that must reproduce a
    pre-review number (doc 18).
    """
    names = [PROTOCOL_LABELS, CORRECTED_LABELS] if prefer_protocol else [CORRECTED_LABELS, PROTOCOL_LABELS]
    for template in names:
        candidate = labels_dir / template.format(pid=pid, sid=sid)
        if candidate.is_file():
            return candidate
    return None


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def resolve_source(video: Path, args: argparse.Namespace, labels_dir: Path) -> Source:
    """Assemble a :class:`Source` from the video path and its collector sidecars.

    Identity comes from the layout ``<pid>/<sid>.mp4`` that ``collect`` writes;
    the recording metadata comes from ``participant.json`` and the session's
    ``<sid>.cues.json`` next to it, because those were measured at record time
    (``camera_position`` in particular is a placement-check verdict, not a
    preference -- doc 19).  Explicit flags win over both.
    """
    video = Path(video)
    if not video.is_file():
        raise SystemExit(f"video not found: {video}")

    pid = args.participant_id or video.parent.name
    sid = args.session_id or video.stem

    cues_path = video.parent / f"{sid}.cues.json"
    participant_path = video.parent / "participant.json"
    session = _read_json(cues_path) if cues_path.is_file() else {}
    participant = _read_json(participant_path) if participant_path.is_file() else {}
    recorded = participant.get("participant", {})

    def pick(flag: Any, *sources: Any, default: Any) -> Any:
        if flag is not None:
            return flag
        for value in sources:
            if value not in (None, ""):
                return value
        return default

    meta = ParticipantMeta(
        participant_id=pid,
        glasses=bool(pick(args.glasses, recorded.get("glasses"), session.get("glasses"), default=False)),
        device_group=str(pick(args.device_group, recorded.get("device_group"),
                              session.get("device_group"), default="laptop_internal_cam")),
        camera_position=str(pick(args.camera_position, recorded.get("camera_position"),
                                 session.get("camera_position"), default="unknown")),
        notes=str(recorded.get("notes", "")),
    )
    lighting = str(pick(args.lighting, session.get("lighting"), participant.get("lighting"),
                        default="normal"))

    labels_path = Path(args.labels) if args.labels else discover_labels(
        labels_dir, pid, sid, prefer_protocol=args.protocol_labels
    )
    segments = load_segments(labels_path) if labels_path else []
    return Source(
        video=video, meta=meta, session_id=sid, lighting=lighting,
        lighting_override=(str(args.lighting) if args.lighting else None),
        segments=segments, labels_path=labels_path,
    )


def build_backbones(cfg: VisionConfig, names: Sequence[str]) -> List[GazeBackbone]:
    """Instantiate each requested backbone (doc 3-2).

    Only the backbone the config actually names inherits its ``checkpoint``:
    pointing a second architecture at the first one's weights would fail deep
    inside ``load_state_dict`` instead of at the download instructions, so the
    others fall back to their own module default.
    """
    built: List[GazeBackbone] = []
    for name in names:
        section = cfg.backbone if name == cfg.backbone.name else replace(
            cfg.backbone, name=name, checkpoint=None, params={}
        )
        built.append(build_backbone(section))
    return built


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


@dataclass
class ExtractStats:
    """What the run saw, for the terminal summary and for sanity."""

    frames: int = 0
    valid: int = 0
    preprocess_ms: float = 0.0
    wall_s: float = 0.0
    invalid_reasons: Counter = field(default_factory=Counter)
    labels: Counter = field(default_factory=Counter)
    backbone_failures: Counter = field(default_factory=Counter)
    #: Summed ``GazeVector.inference_ms`` per backbone (floats, not counts).
    inference_ms: Counter = field(default_factory=Counter)
    first_error: Dict[str, str] = field(default_factory=dict)


def _estimate_sampled_frames(video: Path, analysis_fps: float) -> Optional[int]:
    cap = cv2.VideoCapture(str(video))
    try:
        if not cap.isOpened():
            return None
        count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        fps = cap.get(cv2.CAP_PROP_FPS)
    finally:
        cap.release()
    if not count or not fps or fps <= 0:
        return None
    return max(1, int(round(count / fps * float(analysis_fps))))


def extract(
    source: Source,
    cfg: VisionConfig,
    backbones: Sequence[GazeBackbone],
    *,
    limit: int = 0,
    progress: bool = True,
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[ManifestRecord], ExtractStats]:
    """Run preprocess once per sampled frame and every backbone on the result."""
    index = SegmentIndex(source.segments)
    has_labels = bool(source.segments)
    rows: Dict[str, List[Dict[str, Any]]] = {b.name: [] for b in backbones}
    manifest: List[ManifestRecord] = []
    stats = ExtractStats()

    total = _estimate_sampled_frames(source.video, cfg.preprocess.analysis_fps)
    if limit:
        total = min(total, limit) if total else limit
    bar = None
    if progress:
        from tqdm import tqdm

        bar = tqdm(total=total, unit="frame", desc=source.video.stem, leave=False)

    # Warm every backbone before the first timed frame: a cold first inference
    # is several times slower and would land in the doc 7 latency percentile.
    for backbone in backbones:
        backbone.warmup(2)

    def lighting_for(segment: Optional[SegmentLabel]) -> Optional[str]:
        if source.lighting_override:
            return source.lighting_override
        return None if segment is not None else source.lighting

    started = time.perf_counter()
    frame_path = _repo_relative(source.video)
    with PreprocessPipeline(cfg) as pipeline:
        for frame_id, t_ms, bgr in iter_video_frames(source.video, cfg.preprocess.analysis_fps):
            obs = pipeline.process_bgr(bgr, frame_id, t_ms)
            segment = index.at(t_ms)
            label: Any = segment if segment is not None else (
                GazeLabel.IGNORE.value if has_labels else None
            )

            stats.frames += 1
            stats.preprocess_ms += obs.preprocess_ms
            if obs.face_valid:
                stats.valid += 1
            elif obs.invalid_reason:
                stats.invalid_reasons[obs.invalid_reason] += 1
            stats.labels[segment.label if segment is not None else
                         (GazeLabel.IGNORE.value if has_labels else "UNLABELLED")] += 1

            for backbone in backbones:
                gaze = _predict(backbone, obs, stats)
                if gaze is not None:
                    stats.inference_ms[backbone.name] += gaze.inference_ms
                rows[backbone.name].append(
                    observations_to_rows(
                        obs, gaze, None, source.meta, source.session_id, label,
                        # A segment carries the lighting of its own block (doc
                        # 4-1's lighting_variant changes it mid-take), so only
                        # an explicit flag or a missing segment overrides it.
                        lighting=lighting_for(segment),
                        backbone=backbone.name,
                    )
                )

            manifest.append(
                ManifestRecord(
                    sample_id=make_sample_id(source.participant_id, source.session_id, frame_id),
                    participant_id=source.participant_id,
                    session_id=source.session_id,
                    timestamp_ms=int(t_ms),
                    # The pixels live in the take, not in extracted stills (doc 20
                    # keeps no frame dumps); sample_id carries the frame index.
                    frame_path=frame_path,
                    gaze_label=(segment.label if segment is not None else GazeLabel.IGNORE.value),
                    condition=(segment.condition if segment is not None else "unknown"),
                    glasses=bool(segment.glasses if segment is not None else source.meta.glasses),
                    lighting=((source.lighting_override or segment.lighting)
                              if segment is not None else source.lighting),
                    device_group=(segment.device_group if segment is not None
                                  else source.meta.device_group),
                    label_version=(segment.label_version if segment is not None else "gaze_label_v1"),
                )
            )
            if bar is not None:
                bar.update(1)
            if limit and stats.frames >= limit:
                break

    stats.wall_s = time.perf_counter() - started
    if bar is not None:
        bar.close()
    return rows, manifest, stats


def _predict(backbone: GazeBackbone, obs: FrameObservation, stats: ExtractStats) -> Optional[GazeVector]:
    """Gaze for a usable frame, or ``None``.

    An invalid frame is never offered to a backbone (doc 3-1 already ruled the
    crops untrustworthy).  A backbone that raises costs one row's gaze columns
    rather than the whole extraction, but the count and the first message are
    reported, because a table where every gaze is empty must not look like a
    take where the person was absent.
    """
    if not obs.face_valid:
        return None
    try:
        return backbone.predict_observation(obs)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        stats.backbone_failures[backbone.name] += 1
        stats.first_error.setdefault(backbone.name, f"{type(exc).__name__}: {exc}")
        return None


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extract_features",
        description=(
            "Turn a recorded take plus its doc 4-2 labels into the doc 20 feature "
            "table(s) and the doc 17 manifest."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--video", action="append", required=True, type=Path,
                        help="recorded take; repeatable")
    parser.add_argument("--labels", type=Path, default=None,
                        help="segment file; default: discovered from --labels-dir")
    parser.add_argument("--protocol-labels", action="store_true",
                        help="use the protocol label file even when a corrected one exists")
    parser.add_argument("--backbone", action="append", default=[],
                        help=f"repeatable; default: the configured one. available: "
                             f"{', '.join(available_backbones())}")
    parser.add_argument("--participant-id", default=None, help="default: the video's parent folder")
    parser.add_argument("--session-id", default=None, help="default: the video's file stem")
    parser.add_argument("--glasses", action=argparse.BooleanOptionalAction, default=None,
                        help="override the recorded metadata")
    parser.add_argument("--lighting", default=None, help="override the recorded metadata")
    parser.add_argument("--camera-position", default=None, help="override the recorded metadata")
    parser.add_argument("--device-group", default=None, help="override the recorded metadata")
    parser.add_argument("--analysis-fps", type=float, default=None,
                        help="override preprocess.analysis_fps")
    parser.add_argument("--out-dir", type=Path, default=None, help="default: ai/datasets/features")
    parser.add_argument("--labels-dir", type=Path, default=None, help="default: ai/datasets/labels")
    parser.add_argument("--manifest-dir", type=Path, default=None,
                        help="default: ai/datasets/manifests")
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0, help="stop after N sampled frames (0 = all)")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-manifest", action="store_true")
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="resolve inputs and print the plan without running the model")
    return parser


def _print_source(source: Source, cfg: VisionConfig, out_paths: Dict[str, Path],
                  manifest_path: Optional[Path]) -> None:
    estimate = _estimate_sampled_frames(source.video, cfg.preprocess.analysis_fps)
    print(f"\n{source.video}")
    print(f"  participant={source.participant_id} session={source.session_id} "
          f"glasses={source.meta.glasses} lighting={source.lighting} "
          f"camera_position={source.meta.camera_position} device={source.meta.device_group}")
    if source.labels_path:
        summary = segment_summary(source.segments)
        print(f"  labels    {source.labels_path}")
        print(f"            {summary['n_segments']} segments, "
              f"{summary['total_ms'] / 1000:.1f}s, ignore {summary['ignore_ratio'] * 100:.1f}%, "
              f"{summary['duration_ms_by_label']}")
    else:
        print("  labels    NONE - the 'label' column will be empty and gaze_eval "
              "will have no ground truth")
    print(f"  sampling  {cfg.preprocess.analysis_fps:g} fps -> "
          f"~{estimate if estimate is not None else '?'} frames")
    for name, path in out_paths.items():
        print(f"  table     {name:<16} -> {path}")
    if manifest_path is not None:
        print(f"  manifest  -> {manifest_path}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config_dir)
    if args.analysis_fps is not None:
        cfg.preprocess.analysis_fps = float(args.analysis_fps)

    # Strip each token, not just test it: "--backbone a, b" is a natural thing to
    # type and used to abort with `unknown backbone(s)  b` because the untrimmed
    # token was what got looked up.
    names = [t for group in args.backbone for n in str(group).split(",") if (t := n.strip())]
    names = list(dict.fromkeys(names or [cfg.backbone.name]))
    unknown = [n for n in names if n not in available_backbones()]
    if unknown:
        raise SystemExit(f"unknown backbone(s) {', '.join(unknown)}; "
                         f"available: {', '.join(available_backbones())}")

    out_dir = Path(args.out_dir or (DATASETS_DIR / "features"))
    labels_dir = Path(args.labels_dir or (DATASETS_DIR / "labels"))
    manifest_dir = Path(args.manifest_dir or (DATASETS_DIR / "manifests"))

    sources = [resolve_source(video, args, labels_dir) for video in args.video]
    plans: List[Tuple[Source, Dict[str, Path], Optional[Path]]] = []
    for source in sources:
        tables = {
            name: resolve_table_path(
                out_dir / f"{source.participant_id}_{source.session_id}_{name}.parquet"
            )
            for name in names
        }
        manifest_path = None if args.no_manifest else (
            manifest_dir / f"{source.participant_id}_{source.session_id}.jsonl"
        )
        existing = [p for p in tables.values() if p.exists()]
        if existing and not args.overwrite and not args.dry_run:
            raise SystemExit(
                f"{existing[0]} already exists; pass --overwrite to replace it"
            )
        plans.append((source, tables, manifest_path))

    print(f"backbones: {', '.join(names)}   config_hash={cfg.hash()}")
    for source, tables, manifest_path in plans:
        _print_source(source, cfg, tables, manifest_path)
    if args.dry_run:
        print("\ndry run: no model was loaded and nothing was written")
        return 0

    backbones = build_backbones(cfg, names)
    failed = False
    try:
        for source, tables, manifest_path in plans:
            rows, manifest, stats = extract(
                source, cfg, backbones, limit=args.limit, progress=not args.no_progress
            )
            if stats.frames == 0:
                print(f"{source.video}: no frames sampled")
                failed = True
                continue

            print(f"\n{source.participant_id}_{source.session_id}: {stats.frames} frames in "
                  f"{stats.wall_s:.1f}s, {stats.valid} valid "
                  f"({stats.valid / stats.frames * 100:.0f}%), "
                  f"preprocess {stats.preprocess_ms / stats.frames:.1f} ms/frame")
            if stats.invalid_reasons:
                print(f"  invalid: {dict(stats.invalid_reasons)}")
            print(f"  labels:  {dict(stats.labels)}")
            for name in names:
                table = save_table(rows_to_frame(rows[name]), tables[name])
                mean_ms = stats.inference_ms[name] / max(1, stats.valid)
                print(f"  {name:<16} {mean_ms:5.1f} ms/frame -> {table}")
                if stats.backbone_failures[name]:
                    failed = True
                    print(f"  {name:<16} FAILED on {stats.backbone_failures[name]} frames: "
                          f"{stats.first_error.get(name)}")
            if manifest_path is not None:
                write_manifest(manifest_path, manifest)
                print(f"  manifest -> {manifest_path}")
    finally:
        for backbone in backbones:
            backbone.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
