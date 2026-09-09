"""Scrub a recorded take against its labels and correct them (doc 4-2).

The protocol labels ``collect`` writes say what the participant was *told* to
do.  Usually that is also what they did, but not always -- a glance away, a cue
missed by half a second, a block where someone kept reading after the cue
flipped.  doc 4-2 wants those frames fixed rather than averaged over, and doc 7
is scored against whatever this file says, so a wrong label is a wrong metric.

Two rules this tool will not break
----------------------------------
*The protocol file is never modified.*  Corrections go to a separate
``.segments.corrected.json`` next to it, so the protocol-derived ground truth
stays reproducible from the cue timeline alone and a review can be redone from
scratch.  Writing over the input is refused, not warned about.

*Only whole segments are relabelled.*  Segment boundaries come from the cue
timeline and its guard bands, which are the parts of doc 4-2 that must stay
consistent between the collector and the evaluator; a reviewer who nudges one
boundary by eye produces a file no other take agrees with.  If a boundary is
genuinely wrong, mark the affected segment ``IGNORE``.

Headless use
------------
Every edit goes through :func:`relabel`, so ``--set`` is the same operation as
pressing ``1``/``2``/``3`` and can be scripted or tested without a display::

    ./.venv/Scripts/python.exe -m ai.tools.label_review --labels x.json --dry-run
    ./.venv/Scripts/python.exe -m ai.tools.label_review --labels x.json \\
        --set 8000=IGNORE --set 11000=CAMERA --write --no-window

Keys: SPACE play/pause, ``,``/``.`` step one frame, ``a``/``d`` jump 1 s,
``[``/``]`` previous/next segment, ``1`` CAMERA, ``2`` BOTTOM, ``3`` IGNORE,
``u`` undo, ``w`` write, ``q`` quit.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

# Allow running as a plain script as well as with -m.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "ai" / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ai.tools.pipeline_demo import TextLayer, text_size  # noqa: E402
from vision.config import DATASETS_DIR  # noqa: E402
from vision.data.labels import (  # noqa: E402
    load_segments,
    merge_adjacent,
    save_segments,
    segment_summary,
)
from vision.schemas import GazeLabel, SegmentLabel  # noqa: E402

# BGR, matching ai/tools/pipeline_demo.py.
_WHITE = (255, 255, 255)
_GREY = (170, 170, 170)
_DARK = (32, 32, 32)
_GREEN = (90, 220, 120)
_AMBER = (60, 180, 250)
_RED = (80, 80, 240)

_LABEL_COLOURS = {
    GazeLabel.CAMERA.value: _GREEN,
    GazeLabel.BOTTOM.value: _AMBER,
    GazeLabel.IGNORE.value: (90, 90, 90),
}

_LABEL_KO = {
    GazeLabel.CAMERA.value: "카메라",
    GazeLabel.BOTTOM.value: "대본",
    GazeLabel.IGNORE.value: "제외",
}

#: Written by ``collect``; the corrected copy sits beside it, never over it.
PROTOCOL_SUFFIX = ".segments.json"
CORRECTED_SUFFIX = ".segments.corrected.json"

#: Provenance stamped on every segment of the output file (``SegmentLabel.source``).
CORRECTED_SOURCE = "corrected"

_HEADER_H = 40
_STRIP_H = 96


# --------------------------------------------------------------------------
# Label editing
# --------------------------------------------------------------------------


def _width(text: str, size: int) -> int:
    """Pixel width estimate that also holds for Hangul.

    ``pipeline_demo.text_size`` assumes Latin advance widths; the Korean footer
    is nearly twice that, and a footer that silently runs off the frame takes
    the key legend with it.
    """
    wide = sum(1 for ch in text if ord(ch) > 0x2E80)
    return text_size(text, size) + int(wide * size * 0.45)


def segment_index_at(segments: Sequence[SegmentLabel], t_ms: int) -> Optional[int]:
    """Index of the segment covering ``t_ms`` (half-open), or ``None``."""
    for index, segment in enumerate(segments):
        if segment.contains(int(t_ms)):
            return index
    return None


def relabel(
    segments: Sequence[SegmentLabel], t_ms: int, label: str
) -> Tuple[List[SegmentLabel], Optional[int]]:
    """Return a copy of ``segments`` with the one covering ``t_ms`` relabelled.

    The input is never mutated: the caller keeps the previous list as the undo
    step, and the protocol file it came from must stay readable for a second
    reviewer.  ``None`` as the returned index means no segment covers ``t_ms``.
    """
    target = GazeLabel.coerce(label).value
    index = segment_index_at(segments, t_ms)
    updated = [SegmentLabel(**segment.to_dict()) for segment in segments]
    if index is not None:
        updated[index].label = target
    return updated, index


def finalise(segments: Sequence[SegmentLabel]) -> List[SegmentLabel]:
    """Stamp the whole file ``corrected`` and merge what now agrees.

    Every segment carries the new provenance, not just the edited ones: the file
    as a whole is a review product, and a downstream reader that filters on
    ``source`` must not get a mixture that depends on which segments happened to
    be touched.  Merging afterwards is what keeps a relabelled segment from
    leaving a seam next to an identical neighbour.
    """
    stamped = []
    for segment in segments:
        copy = SegmentLabel(**segment.to_dict())
        copy.source = CORRECTED_SOURCE
        stamped.append(copy)
    return merge_adjacent(stamped)


def diff_segments(
    before: Sequence[SegmentLabel], after: Sequence[SegmentLabel]
) -> List[Tuple[SegmentLabel, str]]:
    """``(original segment, new label)`` for every changed segment."""
    return [
        (old, new.label)
        for old, new in zip(before, after)
        if old.label != new.label
    ]


def parse_set(expression: str) -> Tuple[int, str]:
    """Parse a ``--set`` expression, ``"<t_ms>=<LABEL>"`` or ``"<t>s=<LABEL>"``."""
    if "=" not in expression:
        raise SystemExit(f"--set expects '<t_ms>=<LABEL>', got {expression!r}")
    when, label = expression.split("=", 1)
    when = when.strip().lower()
    try:
        t_ms = int(round(float(when[:-1]) * 1000.0)) if when.endswith("s") else int(when)
    except ValueError:
        raise SystemExit(f"--set: {when!r} is not a timestamp") from None
    return t_ms, GazeLabel.coerce(label).value


# --------------------------------------------------------------------------
# Video access
# --------------------------------------------------------------------------


class VideoScrubber:
    """Random access to a recorded take by timestamp.

    Sequential reads are the common case (playback and single stepping), so a
    seek is only issued when the requested frame is not the one the decoder is
    already positioned on -- seeking every frame makes a long-GOP mp4 crawl.
    """

    def __init__(self, path: Path) -> None:
        self.cap = cv2.VideoCapture(str(path))
        if not self.cap.isOpened():
            raise SystemExit(f"could not open video {path}")
        self.path = path
        self.fps = float(self.cap.get(cv2.CAP_PROP_FPS)) or 30.0
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.duration_ms = int(round(self.frame_count / self.fps * 1000.0))
        self._next_index = 0
        self._cache: Optional[Tuple[int, np.ndarray]] = None

    def frame_step_ms(self) -> int:
        return max(1, int(round(1000.0 / self.fps)))

    def at(self, t_ms: int) -> Optional[np.ndarray]:
        index = int(np.clip(round(t_ms * self.fps / 1000.0), 0, max(0, self.frame_count - 1)))
        if self._cache is not None and self._cache[0] == index:
            return self._cache[1]
        if index != self._next_index:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            self._next_index = index
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return None
        self._next_index = index + 1
        self._cache = (index, frame)
        return frame

    def close(self) -> None:
        self.cap.release()


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


@dataclass
class ReviewState:
    """Everything the overlay needs that is not the pixels.

    ``segments`` stays index-parallel with ``protocol`` for the whole session --
    editing only ever replaces a label, never splits or merges an interval -- so
    the overlay can show "was CAMERA" by index.  Merging happens once, in
    :func:`finalise`, on the way to disk.
    """

    t_ms: int
    segments: List[SegmentLabel]
    protocol: List[SegmentLabel]
    duration_ms: int
    playing: bool
    out_path: Path
    #: Labels last written to ``out_path`` this session; ``None`` until a write.
    saved_labels: Optional[List[str]] = None

    @property
    def dirty(self) -> bool:
        """True when the current labels are not the ones on disk."""
        baseline = (
            self.saved_labels
            if self.saved_labels is not None
            else [segment.label for segment in self.protocol]
        )
        return [segment.label for segment in self.segments] != baseline

    def mark_saved(self) -> None:
        self.saved_labels = [segment.label for segment in self.segments]


def render_review(
    frame: Optional[np.ndarray],
    state: ReviewState,
    size: Tuple[int, int],
    *,
    korean: bool = True,
) -> np.ndarray:
    """Draw the frame, the label bar and the whole-take segment strip."""
    width, height = size
    view = (
        cv2.resize(frame, (width, height))
        if frame is not None
        else np.full((height, width, 3), 18, dtype=np.uint8)
    )
    layer = TextLayer()
    ko = korean and layer.unicode_ready

    index = segment_index_at(state.segments, state.t_ms)
    current = state.segments[index] if index is not None else None
    original = state.protocol[index] if (index is not None and index < len(state.protocol)) else None

    cv2.rectangle(view, (0, 0), (width, _HEADER_H), _DARK, -1)
    head = f"{state.t_ms / 1000:7.2f} / {state.duration_ms / 1000:.1f}s"
    if current is not None:
        head += f"   seg {index + 1}/{len(state.segments)}   {current.condition}"
    layer.put((12, 9), head, 18, _WHITE)
    if state.dirty:
        layer.put((width - _width("UNSAVED", 18) - 12, 9), "UNSAVED", 18, _RED)

    if current is not None:
        name = (_LABEL_KO[current.label] if ko else current.label)
        cv2.rectangle(view, (0, _HEADER_H), (int(width * 0.42), _HEADER_H + 72), _DARK, -1)
        layer.put((12, _HEADER_H + 8), name, 34, _LABEL_COLOURS[current.label])
        if original is not None and original.label != current.label:
            was = f"({'was ' if not ko else '원래 '}{original.label})"
            layer.put((12, _HEADER_H + 48), was, 16, _GREY)

    _draw_strip(view, layer, state, width, height)

    footer = (
        "SPACE 재생  , . 프레임  a d 1초  [ ] 구간  1 카메라  2 대본  3 제외  u 되돌리기  w 저장  q 종료"
        if ko
        else "SPACE play  , . frame  a d 1s  [ ] segment  1 CAMERA  2 BOTTOM  3 IGNORE  u undo  w write  q quit"
    )
    if _width(footer, 14) > width - 24:
        footer = (
            "SPACE 재생  , . a d 이동  [ ] 구간  1/2/3 라벨  u 취소  w 저장  q 종료"
            if ko
            else "SPACE play  , . a d seek  [ ] seg  1/2/3 label  u undo  w write  q quit"
        )
    layer.put((12, height - 22), footer, 14, _GREY)
    return layer.flush(view)


def _draw_strip(view, layer, state: ReviewState, width: int, height: int) -> None:
    """The whole take as one coloured bar: where the labels are, and the playhead."""
    top = height - _STRIP_H
    cv2.rectangle(view, (0, top), (width, height), _DARK, -1)
    y0, y1 = top + 10, top + 34
    span = max(1, state.duration_ms)

    def x_of(t_ms: int) -> int:
        return int(np.clip(t_ms / span, 0.0, 1.0) * (width - 1))

    for position, segment in enumerate(state.segments):
        colour = _LABEL_COLOURS[segment.label]
        cv2.rectangle(view, (x_of(segment.start_ms), y0), (x_of(segment.end_ms), y1), colour, -1)
        # A corrected segment gets a red underline, so a reviewer can see at a
        # glance what this pass has already touched.
        if position < len(state.protocol) and state.protocol[position].label != segment.label:
            cv2.rectangle(view, (x_of(segment.start_ms), y1), (x_of(segment.end_ms), y1 + 4), _RED, -1)

    playhead = x_of(state.t_ms)
    cv2.line(view, (playhead, y0 - 6), (playhead, y1 + 8), _WHITE, 2)

    x = 12
    for label in (GazeLabel.CAMERA.value, GazeLabel.BOTTOM.value, GazeLabel.IGNORE.value):
        cv2.rectangle(view, (x, y1 + 12), (x + 16, y1 + 24), _LABEL_COLOURS[label], -1)
        layer.put((x + 22, y1 + 10), label, 14, _GREY)
        x += 34 + _width(label, 14)


# --------------------------------------------------------------------------
# Interactive loop
# --------------------------------------------------------------------------


def run_window(
    scrubber: VideoScrubber,
    state: ReviewState,
    *,
    korean: bool,
    window: str = "Pitch Coach - label review",
) -> bool:
    """Drive the review UI.  Returns True when the corrected file was written."""
    undo: List[List[SegmentLabel]] = []
    written = False
    step = scrubber.frame_step_ms()
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    frame = scrubber.at(state.t_ms)
    size = (frame.shape[1], frame.shape[0]) if frame is not None else (1280, 720)

    while True:
        frame = scrubber.at(state.t_ms)
        view = render_review(frame, state, size, korean=korean)
        cv2.imshow(window, view)
        key = cv2.waitKey(step if state.playing else 30) & 0xFF

        if key in (ord("q"), 27):
            break
        if key == ord(" "):
            state.playing = not state.playing
        elif key == ord("."):
            state.playing, state.t_ms = False, min(state.duration_ms, state.t_ms + step)
        elif key == ord(","):
            state.playing, state.t_ms = False, max(0, state.t_ms - step)
        elif key == ord("d"):
            state.playing, state.t_ms = False, min(state.duration_ms, state.t_ms + 1000)
        elif key == ord("a"):
            state.playing, state.t_ms = False, max(0, state.t_ms - 1000)
        elif key == ord("]"):
            state.playing, state.t_ms = False, _next_segment_start(state, +1)
        elif key == ord("["):
            state.playing, state.t_ms = False, _next_segment_start(state, -1)
        elif key in (ord("1"), ord("2"), ord("3")):
            label = {ord("1"): GazeLabel.CAMERA, ord("2"): GazeLabel.BOTTOM,
                     ord("3"): GazeLabel.IGNORE}[key].value
            updated, index = relabel(state.segments, state.t_ms, label)
            if index is not None:
                undo.append(state.segments)
                state.segments = updated
        elif key == ord("u") and undo:
            state.segments = undo.pop()
        elif key == ord("w"):
            save_segments(state.out_path, finalise(state.segments))
            state.mark_saved()
            written = True
            print(f"wrote {state.out_path}")

        if state.playing:
            state.t_ms += step
            if state.t_ms >= state.duration_ms:
                state.t_ms, state.playing = state.duration_ms, False

    cv2.destroyWindow(window)
    if state.dirty:
        print("quit with unsaved corrections; nothing was written (press w to save)")
    return written


def _next_segment_start(state: ReviewState, direction: int) -> int:
    starts = [segment.start_ms for segment in state.segments]
    if direction > 0:
        later = [s for s in starts if s > state.t_ms]
        return later[0] if later else state.duration_ms
    earlier = [s for s in starts if s < state.t_ms]
    return earlier[-1] if earlier else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def default_output(labels: Path) -> Path:
    """Corrected-file path for a protocol label file, in the same directory."""
    name = labels.name
    if name.endswith(CORRECTED_SUFFIX):
        return labels
    if name.endswith(PROTOCOL_SUFFIX):
        return labels.with_name(name[: -len(PROTOCOL_SUFFIX)] + CORRECTED_SUFFIX)
    return labels.with_name(labels.stem + CORRECTED_SUFFIX)


def discover_labels(labels_dir: Path, pid: str, sid: str) -> Optional[Path]:
    candidate = labels_dir / f"{pid}_{sid}{PROTOCOL_SUFFIX}"
    return candidate if candidate.is_file() else None


def print_table(segments: Sequence[SegmentLabel]) -> None:
    print(f"{'idx':>4} {'start':>9} {'end':>9} {'dur':>7}  {'label':<7} {'condition':<26} source")
    for index, segment in enumerate(segments):
        print(
            f"{index:>4} {segment.start_ms / 1000:>9.2f} {segment.end_ms / 1000:>9.2f} "
            f"{segment.duration_ms / 1000:>7.2f}  {segment.label:<7} "
            f"{segment.condition:<26} {segment.source}"
        )
    summary = segment_summary(segments)
    print(f"     {summary['n_segments']} segments, {summary['total_ms'] / 1000:.1f}s, "
          f"ignore {summary['ignore_ratio'] * 100:.1f}%, {summary['duration_ms_by_label']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="label_review",
        description=(
            "Review a take against its doc 4-2 segment labels and write corrections "
            "to a separate file; the protocol original is never modified."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--video", type=Path, default=None,
                        help="recorded take; required for the window, optional for --set")
    parser.add_argument("--labels", type=Path, default=None,
                        help="segment file; default: discovered from --labels-dir")
    parser.add_argument("--labels-dir", type=Path, default=None, help="default: ai/datasets/labels")
    parser.add_argument("--participant-id", default=None, help="for label discovery")
    parser.add_argument("--session-id", default=None, help="for label discovery")
    parser.add_argument("--out", type=Path, default=None,
                        help="default: <labels>.corrected.json beside the original")
    parser.add_argument("--set", action="append", default=[], metavar="T_MS=LABEL",
                        help="relabel the segment covering T_MS; repeatable, headless")
    parser.add_argument("--write", action="store_true", help="write the corrected file after --set")
    parser.add_argument("--start-ms", type=int, default=0, help="open the scrubber here")
    parser.add_argument("--save-frame", type=Path, default=None,
                        help="render one review frame to PNG; the window still opens afterwards "
                             "unless --no-window is also given")
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--english", action="store_true", help="force English on-screen text")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the segment table (after any --set) and exit")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    labels_dir = Path(args.labels_dir or (DATASETS_DIR / "labels"))

    labels_path = Path(args.labels) if args.labels else None
    if labels_path is None:
        if not (args.participant_id and args.session_id):
            raise SystemExit("pass --labels, or --participant-id and --session-id")
        labels_path = discover_labels(labels_dir, args.participant_id, args.session_id)
    if labels_path is None or not labels_path.is_file():
        raise SystemExit(f"label file not found: {labels_path}")

    out_path = Path(args.out) if args.out else default_output(labels_path)
    if out_path.resolve() == labels_path.resolve():
        raise SystemExit(
            f"refusing to overwrite the input labels ({labels_path}); "
            "corrections belong in a separate file"
        )

    protocol = load_segments(labels_path)
    if not protocol:
        raise SystemExit(f"{labels_path} contains no segments")
    segments = [SegmentLabel(**segment.to_dict()) for segment in protocol]

    for expression in args.set:
        t_ms, label = parse_set(expression)
        segments, index = relabel(segments, t_ms, label)
        if index is None:
            print(f"--set {expression}: no segment covers {t_ms} ms; ignored")
            continue
        print(f"--set {t_ms} ms -> {label}  (segment {index}: "
              f"{protocol[index].start_ms}-{protocol[index].end_ms} ms, was {protocol[index].label})")

    print(f"labels  {labels_path}")
    print(f"output  {out_path}")

    scrubber: Optional[VideoScrubber] = None
    duration_ms = max(segment.end_ms for segment in segments)
    if args.video is not None:
        scrubber = VideoScrubber(Path(args.video))
        duration_ms = max(duration_ms, scrubber.duration_ms)
        print(f"video   {args.video}  {scrubber.frame_count} frames @ {scrubber.fps:g} fps "
              f"({scrubber.duration_ms / 1000:.1f}s)")

    state = ReviewState(
        t_ms=int(np.clip(args.start_ms, 0, duration_ms)),
        segments=segments,
        protocol=protocol,
        duration_ms=duration_ms,
        playing=False,
        out_path=out_path,
    )

    try:
        if args.save_frame is not None:
            frame = scrubber.at(state.t_ms) if scrubber is not None else None
            size = (frame.shape[1], frame.shape[0]) if frame is not None else (1280, 720)
            Path(args.save_frame).parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(args.save_frame),
                        render_review(frame, state, size, korean=not args.english))
            print(f"frame   -> {args.save_frame}")

        if args.dry_run:
            print_table(state.segments)
            changes = diff_segments(protocol, state.segments)
            print(f"changed {len(changes)} segment(s)"
                  + ("" if not changes else ": "
                     + ", ".join(f"{s.start_ms}-{s.end_ms} {s.label}->{new}" for s, new in changes)))
            return 0

        if args.write:
            save_segments(out_path, finalise(state.segments))
            state.mark_saved()
            print(f"wrote {out_path}")
            print_table(load_segments(out_path))
        elif args.set and args.no_window:
            print("--set applied in memory only; add --write to save the corrected file")

        if not args.no_window:
            if scrubber is None:
                raise SystemExit("--video is required for the review window (or use --no-window)")
            run_window(scrubber, state, korean=not args.english)
    finally:
        if scrubber is not None:
            scrubber.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
