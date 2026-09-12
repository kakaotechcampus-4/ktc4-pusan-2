"""Guided recording of one dataset take (doc 4-1).

The collector is the only place that knows what the participant was *told* to
do, so it is the only place that can produce ground truth.  It therefore writes
three things next to the video and never anything else:

    <out-root>/<pid>/<sid>.mp4          raw frames, unmirrored
    <out-root>/<pid>/<sid>.cues.json    the cue timeline that was shown
    <out-root>/<pid>/participant.json   doc 4-1 ParticipantMeta + lighting

and then hands the cue timeline to ``vision.data.labels`` for the doc 4-2
segment file.  Labels are never authored here: the guard band around a cue
change is a dataset rule, not a UI detail, and duplicating it would let the
collector and the evaluator disagree about which frames count.

Three details that are easy to get wrong and expensive to discover later:

*Container time must equal capture time.*  The label file is expressed in
milliseconds of the recording.  A webcam that delivers 27 fps into a writer
opened at 30 fps produces a file whose clock runs ~10 % fast, which shifts every
label by seconds near the end of a 5-minute take.  :class:`PacedWriter` pins the
file's frame index to the wall clock instead, duplicating or dropping frames as
needed, and reports how many of each it had to do.

*The countdown is recorded, so it must be labelled.*  Frames captured while the
"3, 2, 1" runs carry no instruction, so the timeline emits an explicit
``IGNORE`` cue for them rather than leaving a gap that ``label_at`` would have
to guess at.

*Camera placement decides what CAMERA and BOTTOM mean.*  A lens below the screen
inverts the two classes without failing anything (doc 19's webcam-position
bucket), so ``--camera-position auto`` runs the doc-19-up-front check from
``vision.runtime.placement`` and records its verdict into the participant
metadata, where doc 17's manifest and doc 19's buckets can see it.

Run it::

    ./.venv/Scripts/python.exe -m ai.tools.collect --participant-id P01
    ./.venv/Scripts/python.exe -m ai.tools.collect --participant-id P01 --dry-run
    ./.venv/Scripts/python.exe -m ai.tools.collect --list-conditions

Keys while recording: Q (or ESC) aborts, keeping everything captured so far.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import cv2
import numpy as np

# Allow running as a plain script as well as with -m.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "ai" / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ai.tools.pipeline_demo import PipelineDemo, Stage, TextLayer, text_size  # noqa: E402
from vision.config import (  # noqa: E402
    DATASETS_DIR,
    REPO_ROOT,
    CollectionConfig,
    ProtocolCondition,
    VisionConfig,
    load_config,
)
from vision.data.labels import save_segments, segment_summary, segments_from_cue_timeline  # noqa: E402
from vision.runtime.placement import PlacementCheckResult  # noqa: E402
from vision.runtime.session import VisionSession  # noqa: E402
from vision.schemas import GazeLabel, ParticipantMeta  # noqa: E402

# BGR, matching ai/tools/pipeline_demo.py so the two screens look like one tool.
_WHITE = (255, 255, 255)
_GREY = (170, 170, 170)
_DARK = (32, 32, 32)
_GREEN = (90, 220, 120)
_AMBER = (60, 180, 250)
_RED = (80, 80, 240)

_CUE_COLOURS = {
    GazeLabel.CAMERA.value: _GREEN,
    GazeLabel.BOTTOM.value: _AMBER,
    GazeLabel.IGNORE.value: _GREY,
}

_CUE_TITLES = {
    GazeLabel.CAMERA.value: ("카메라를 보세요", "LOOK AT THE CAMERA"),
    GazeLabel.BOTTOM.value: ("대본을 보세요", "LOOK AT YOUR SCRIPT"),
    GazeLabel.IGNORE.value: ("준비하세요", "GET READY"),
}

#: ``ParticipantMeta.camera_position`` value for each placement verdict.
#: ``INCONCLUSIVE`` becomes ``unknown`` rather than the expected ``top_center``:
#: doc 19 buckets treat ``unknown`` as centred, but recording a guess as a
#: measurement would make the bucket report describe the wrong geometry.
_PLACEMENT_TO_POSITION = {
    "TOP": "top_center",
    "BOTTOM": "bottom",
    "SIDE_LEFT": "side_left",
    "SIDE_RIGHT": "side_right",
    "INCONCLUSIVE": "unknown",
}

CAMERA_POSITIONS = ("auto", "top_center", "bottom", "side_left", "side_right", "unknown")

#: Lighting values doc 19's low-light bucket recognises, plus the neutral ones.
LIGHTING_VALUES = ("normal", "dim", "dark", "bright", "backlit", "backlit_dim")

SESSION_FILE_SCHEMA = "gaze_cues_v1"
PARTICIPANT_FILE_SCHEMA = "participant_meta_v1"

#: Where dry runs write, so a rehearsal never lands a video-less session in the
#: real dataset tree.  ``--out-root`` overrides it.
DRY_RUN_SUBDIR = "_dryrun"


# --------------------------------------------------------------------------
# Protocol
# --------------------------------------------------------------------------


# eq=False so that identity, not field equality, decides which block a phase
# belongs to: two configured conditions can be byte-identical (an operator
# repeating a block), and value equality would then attribute both to the first.
@dataclass(frozen=True, eq=False)
class CueBlock:
    """One recording block and the cue schedule inside it (doc 4-1)."""

    key: str
    seconds: float
    prompt: str
    speaking: bool
    head_motion: bool
    #: ``(offset_s, cue)`` pairs relative to the end of this block's countdown.
    cues: Tuple[Tuple[float, str], ...]


@dataclass(frozen=True)
class Phase:
    """A stretch of recording during which exactly one cue was on screen."""

    index: int
    start_ms: int
    end_ms: int
    cue: str
    block: CueBlock
    #: True while the countdown runs; those frames are cued ``IGNORE``.
    counting: bool

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def block_from_condition(condition: ProtocolCondition) -> CueBlock:
    """Expand one configured condition into its cue schedule.

    A block with ``alternate_period_s`` flips CAMERA/BOTTOM on that period,
    starting from ``cue`` when the config names one; the doc 4-1 alternating
    blocks leave it empty and start on CAMERA.
    """
    seconds = float(condition.seconds)
    period = float(condition.alternate_period_s or 0.0)
    first = GazeLabel.coerce(condition.cue).value if condition.cue else GazeLabel.CAMERA.value

    if period > 0.0:
        pair = (GazeLabel.CAMERA.value, GazeLabel.BOTTOM.value)
        start = pair.index(first) if first in pair else 0
        n = max(1, int(np.ceil(seconds / period)))
        cues = tuple((i * period, pair[(start + i) % 2]) for i in range(n) if i * period < seconds)
    else:
        cues = ((0.0, first),)

    return CueBlock(
        key=condition.key,
        seconds=seconds,
        prompt=condition.prompt,
        speaking=bool(condition.speaking),
        head_motion=bool(condition.head_motion),
        cues=cues,
    )


def calibration_blocks(cfg: VisionConfig) -> List[ProtocolCondition]:
    """The two ``calib_*`` blocks doc 4-3 expects the collector to record.

    ``splits.calibration_frames`` prefers a condition whose name starts with
    ``calib`` over its label-run heuristic, so a take that opens with these two
    blocks makes the per-user protocol explicit instead of inferred.

    Length: the doc 5-1 stare plus a guard band at each end, because doc 4-2
    cuts +/- ``transition_guard_ms`` around every cue change and the leftover has
    to still clear ``min_samples_per_class`` at ``analysis_fps``.  One extra
    second absorbs the frames a blink costs.
    """
    guard_s = cfg.collection.transition_guard_ms / 1000.0
    return [
        ProtocolCondition(
            key="calib_camera",
            seconds=float(cfg.calibration.camera_seconds) + 2.0 * guard_s + 1.0,
            cue=GazeLabel.CAMERA.value,
            prompt="Calibration: look straight at the webcam and hold still.",
        ),
        ProtocolCondition(
            key="calib_bottom",
            seconds=float(cfg.calibration.bottom_seconds) + 2.0 * guard_s + 1.0,
            cue=GazeLabel.BOTTOM.value,
            prompt="Calibration: look down at your script and hold still.",
        ),
    ]


class Protocol:
    """The whole take as an ordered list of phases on a single clock."""

    def __init__(self, blocks: Sequence[CueBlock], countdown_s: float) -> None:
        if not blocks:
            raise ValueError("protocol has no blocks; check --conditions")
        self.blocks = list(blocks)
        self.countdown_s = float(countdown_s)
        self.phases = self._build()
        self.total_ms = self.phases[-1].end_ms

    def _build(self) -> List[Phase]:
        phases: List[Phase] = []
        cursor = 0.0
        for block in self.blocks:
            if self.countdown_s > 0.0:
                phases.append(
                    Phase(
                        index=len(phases),
                        start_ms=int(round(cursor * 1000.0)),
                        end_ms=int(round((cursor + self.countdown_s) * 1000.0)),
                        # The countdown is recorded but uninstructed (module docstring).
                        cue=GazeLabel.IGNORE.value,
                        block=block,
                        counting=True,
                    )
                )
                cursor += self.countdown_s
            block_end = cursor + block.seconds
            for position, (offset, cue) in enumerate(block.cues):
                start = cursor + offset
                stop = (
                    cursor + block.cues[position + 1][0]
                    if position + 1 < len(block.cues)
                    else block_end
                )
                phases.append(
                    Phase(
                        index=len(phases),
                        start_ms=int(round(start * 1000.0)),
                        end_ms=int(round(min(stop, block_end) * 1000.0)),
                        cue=cue,
                        block=block,
                        counting=False,
                    )
                )
            cursor = block_end
        return phases

    def phase_at(self, t_ms: int) -> Phase:
        """Phase covering ``t_ms``; the last phase for anything past the end."""
        for phase in self.phases:
            if phase.start_ms <= t_ms < phase.end_ms:
                return phase
        return self.phases[-1] if t_ms >= self.total_ms else self.phases[0]

    def block_bounds(self, block: CueBlock) -> Tuple[int, int]:
        owned = [p for p in self.phases if p.block is block]
        return owned[0].start_ms, owned[-1].end_ms

    def cue_timeline(self, end_ms: Optional[int] = None, *, lighting: str = "normal") -> List[Dict[str, Any]]:
        """The doc 4-2 input: one entry per cue change plus a final end marker.

        ``end_ms`` truncates an aborted take.  Entries at or after the cut are
        dropped, so the timeline never claims a cue the participant never saw.
        """
        stop = int(self.total_ms if end_ms is None else end_ms)
        entries: List[Dict[str, Any]] = []
        for phase in self.phases:
            if phase.start_ms >= stop:
                break
            entries.append(
                {
                    "t_ms": int(phase.start_ms),
                    "cue": phase.cue,
                    "condition": phase.block.key,
                    "lighting": lighting,
                    "speaking": phase.block.speaking,
                    "head_motion": phase.block.head_motion,
                }
            )
        entries.append({"t_ms": stop, "cue": "END"})
        return entries

    def plan_rows(self) -> List[Tuple[str, str, str, str]]:
        """``(block, window, cue schedule, prompt)`` for the terminal plan table."""
        rows = []
        for block in self.blocks:
            start, end = self.block_bounds(block)
            cues = [p.cue for p in self.phases if p.block is block and not p.counting]
            schedule = cues[0] if len(set(cues)) == 1 else " ".join(c[0] for c in cues)
            flags = "".join(["S" if block.speaking else "", "H" if block.head_motion else ""])
            rows.append(
                (
                    f"{block.key}{(' [' + flags + ']') if flags else ''}",
                    f"{start / 1000:6.1f}-{end / 1000:6.1f}s",
                    schedule,
                    block.prompt,
                )
            )
        return rows


def build_protocol(
    cfg: VisionConfig,
    *,
    keys: Optional[Sequence[str]] = None,
    with_calibration: bool = True,
) -> Protocol:
    """Assemble the recording protocol from config, optionally filtered."""
    conditions = (calibration_blocks(cfg) if with_calibration else []) + list(
        cfg.collection.conditions
    )
    if not conditions:
        raise SystemExit(
            "collection.yaml lists no conditions and --no-calibration-block was given; "
            "nothing to record"
        )
    if keys:
        wanted = [k.strip() for k in keys if k.strip()]
        known = {c.key for c in conditions}
        unknown = [k for k in wanted if k not in known]
        if unknown:
            raise SystemExit(
                f"unknown condition(s) {', '.join(unknown)}; available: "
                f"{', '.join(sorted(known))}"
            )
        conditions = [c for c in conditions if c.key in wanted]
    return Protocol([block_from_condition(c) for c in conditions], cfg.collection.countdown_s)


# --------------------------------------------------------------------------
# Frame sources and the writer
# --------------------------------------------------------------------------


class CameraSource:
    """The webcam, opened once and shared by the placement check and the take."""

    def __init__(self, index: int, width: int, height: int, fps: int) -> None:
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            raise SystemExit(f"could not open camera index {index}")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        self.cap = cap
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            raise SystemExit(f"camera index {index} opened but delivered no frame")
        self.size = (int(frame.shape[1]), int(frame.shape[0]))
        self._first = frame

    def frames(self) -> Iterator[Tuple[int, np.ndarray]]:
        """Yield ``(t_ms, bgr)`` on a wall clock started at the first frame.

        Wall clock rather than a frame counter: the writer, the cue timeline and
        the label file all have to agree on when a cue was on screen, and a
        webcam's delivered rate is not its nominal one.
        """
        start = time.perf_counter()
        if self._first is not None:
            frame, self._first = self._first, None
            yield 0, frame
        while True:
            ok, frame = self.cap.read()
            if not ok or frame is None:
                return
            yield int((time.perf_counter() - start) * 1000.0), frame

    def close(self) -> None:
        self.cap.release()


def rebase(frames: Iterator[Tuple[int, np.ndarray]]) -> Iterator[Tuple[int, np.ndarray]]:
    """Restart a source's clock at zero, for the take that follows a setup stage."""
    origin: Optional[int] = None
    for t_ms, frame in frames:
        if origin is None:
            origin = t_ms
        yield t_ms - origin, frame


def synthetic_frames(
    size: Tuple[int, int], fps: float, video: Optional[Path] = None
) -> Iterator[Tuple[int, np.ndarray]]:
    """Dry-run source: a virtual clock over a looped video or a placeholder card.

    The clock is virtual so a 5-minute protocol rehearses in seconds, and the
    frames still go through the real HUD so the overlay is exercised rather than
    described.
    """
    width, height = size
    interval_ms = 1000.0 / float(fps)
    pool: List[np.ndarray] = []
    if video is not None:
        cap = cv2.VideoCapture(str(video))
        try:
            while len(pool) < 120:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                pool.append(cv2.resize(frame, (width, height)))
        finally:
            cap.release()
    if not pool:
        card = np.zeros((height, width, 3), dtype=np.uint8)
        card[:] = np.linspace(24, 72, height, dtype=np.uint8)[:, None, None]
        cv2.putText(
            card, "DRY RUN - no camera", (int(width * 0.22), height // 2),
            cv2.FONT_HERSHEY_SIMPLEX, width / 900.0, (90, 90, 90), 2, cv2.LINE_AA,
        )
        pool.append(card)

    index = 0
    while True:
        yield int(round(index * interval_ms)), pool[index % len(pool)].copy()
        index += 1


class PacedWriter:
    """Video writer whose container clock tracks the capture wall clock.

    ``cv2.VideoWriter`` stamps frames at a fixed nominal rate, so a camera that
    runs slow silently compresses the recording's timeline and shifts every
    label written against it.  Each frame is therefore written to the index its
    timestamp says it belongs at -- repeated when the camera fell behind, skipped
    when it ran ahead -- and both counts are reported so a take recorded on a
    struggling machine is visible rather than merely wrong.
    """

    def __init__(self, path: Path, fps: int, size: Tuple[int, int]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), float(fps), size)
        if not self.writer.isOpened():
            raise SystemExit(f"could not open a video writer for {path}")
        self.path = path
        self.fps = float(fps)
        self.written = 0
        self.duplicated = 0
        self.dropped = 0

    def write(self, frame: np.ndarray, t_ms: int) -> None:
        target = int(round(t_ms * self.fps / 1000.0)) + 1
        if target <= self.written:
            self.dropped += 1
            return
        repeats = target - self.written
        self.duplicated += repeats - 1
        for _ in range(repeats):
            self.writer.write(frame)
        self.written = target

    def close(self) -> None:
        self.writer.release()


# --------------------------------------------------------------------------
# On-screen guidance
# --------------------------------------------------------------------------


class CollectorHud:
    """Draws the cue, the countdown, the prompt and the progress bars."""

    def __init__(self, korean: bool = True) -> None:
        self.korean = korean and TextLayer().unicode_ready

    def render(
        self,
        bgr: np.ndarray,
        phase: Phase,
        t_ms: int,
        protocol: Protocol,
        *,
        frames_written: int,
        recording: bool,
    ) -> np.ndarray:
        # The file keeps raw frames (placement yaw signs are read on them);
        # only the preview is mirrored so the participant sees a selfie view.
        view = cv2.flip(bgr, 1)
        h, w = view.shape[:2]
        layer = TextLayer()
        ko = self.korean
        block = phase.block
        block_start, block_end = protocol.block_bounds(block)
        block_index = protocol.blocks.index(block) + 1

        # Only the text bands are darkened, never the whole preview: the
        # participant uses this window to check they are still in frame, and a
        # dimmed selfie view is exactly what hides a face drifting out of it.
        band = view[max(0, h // 2 - 130) : min(h, h // 2 + 20)]
        if band.size:
            cv2.addWeighted(band, 0.38, np.zeros_like(band), 0.0, 0.0, band)
        cv2.rectangle(view, (0, 0), (w, 40), _DARK, -1)
        header = (
            f"{block_index}/{len(protocol.blocks)}  {block.key}"
            + ("  발화" if (ko and block.speaking) else ("  speaking" if block.speaking else ""))
            + ("  머리움직임" if (ko and block.head_motion) else ("  head motion" if block.head_motion else ""))
        )
        layer.put((12, 9), header, 19, _WHITE)
        clock = f"{t_ms / 1000:5.1f} / {protocol.total_ms / 1000:.0f}s"
        layer.put((_right_x(w, clock, 18), 10), clock, 18, _GREY)

        title_ko, title_en = _CUE_TITLES[phase.cue]
        colour = _CUE_COLOURS[phase.cue]
        title = title_ko if ko else title_en
        layer.put(((w - text_size(title, 40)) // 2, h // 2 - 120), title, 40, colour)

        if phase.counting:
            remain = int(np.ceil((phase.end_ms - t_ms) / 1000.0))
            digit = str(max(remain, 1))
            layer.put(((w - text_size(digit, 72)) // 2, h // 2 - 55), digit, 72, _WHITE)
        elif block.prompt:
            for offset, line in enumerate(_wrap(block.prompt, 54)):
                layer.put(((w - text_size(line, 18)) // 2, h // 2 - 60 + offset * 26), line, 18, _GREY)

        self._bar(view, layer, w, h - 96, "block", (t_ms - block_start) / max(1, block_end - block_start), colour)
        self._bar(view, layer, w, h - 62, "take", t_ms / max(1, protocol.total_ms), _WHITE)

        cv2.rectangle(view, (0, h - 34), (w, h), _DARK, -1)
        if recording:
            cv2.circle(view, (22, h - 17), 8, _RED, -1)
            status = f"REC  {frames_written} frames"
        else:
            status = "DRY RUN - nothing is being written to video"
        layer.put((40 if recording else 12, h - 27), status, 16, _WHITE)
        quit_hint = "Q 중단" if ko else "Q abort"
        layer.put((_right_x(w, quit_hint, 16), h - 27), quit_hint, 16, _GREY)
        return layer.flush(view)

    @staticmethod
    def _bar(view, layer, w: int, y: int, name: str, fraction: float, colour) -> None:
        x0, bar_w = int(w * 0.12), int(w * 0.76)
        cv2.rectangle(view, (x0, y), (x0 + bar_w, y + 12), (64, 64, 64), -1)
        filled = int(bar_w * float(np.clip(fraction, 0.0, 1.0)))
        cv2.rectangle(view, (x0, y), (x0 + filled, y + 12), colour, -1)
        layer.put((x0 - text_size(name, 14) - 8, y - 2), name, 14, _GREY)


def _right_x(w: int, text: str, size: int, pad: int = 12) -> int:
    """x for a right-aligned string.

    ``pipeline_demo.text_size`` assumes Latin advance widths; Hangul is
    full-width, so a right-aligned Korean string estimated that way runs off the
    frame.  Centring tolerates the error (it is symmetric), this does not.
    """
    wide = sum(1 for ch in text if ord(ch) > 0x2E80)
    return w - text_size(text, size) - int(wide * size * 0.45) - pad


def _wrap(text: str, width: int) -> List[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


# --------------------------------------------------------------------------
# The take
# --------------------------------------------------------------------------


@dataclass
class Take:
    """What actually happened, as opposed to what the protocol asked for."""

    end_ms: int
    frames_captured: int
    frames_written: int
    duplicated: int
    dropped: int
    #: completed | operator_quit | source_ended.  A take that stopped early is
    #: still written out, truncated: half a protocol is data, a lost one is not.
    reason: str

    @property
    def aborted(self) -> bool:
        return self.reason != "completed"


def record_take(
    protocol: Protocol,
    frames: Iterator[Tuple[int, np.ndarray]],
    hud: CollectorHud,
    writer: Optional[PacedWriter],
    *,
    window: Optional[str] = None,
    overlay_dir: Optional[Path] = None,
    render: bool = True,
) -> Take:
    """Run the protocol against a frame source until it ends or ``q`` is pressed.

    ``render=False`` skips the overlay entirely.  It costs ~10 ms a frame, which
    at 30 fps is a third of the capture budget, and an unattended run with no
    window and no overlay dump has nobody to show it to.
    """
    captured = 0
    reason = "source_ended"
    last_t = 0
    last_frame: Optional[np.ndarray] = None
    seen: set = set()

    for t_ms, frame in frames:
        if t_ms >= protocol.total_ms:
            reason = "completed"
            break
        phase = protocol.phase_at(t_ms)
        captured += 1
        last_t, last_frame = t_ms, frame
        if writer is not None:
            writer.write(frame, t_ms)

        view = (
            hud.render(
                frame, phase, t_ms, protocol,
                frames_written=(writer.written if writer else 0),
                recording=writer is not None,
            )
            if render
            else frame
        )
        if overlay_dir is not None and phase.index not in seen:
            seen.add(phase.index)
            cv2.imwrite(
                str(overlay_dir / f"{phase.index:02d}_{phase.block.key}_{phase.cue}.png"), view
            )
        if window is not None:
            cv2.imshow(window, view)
            if (cv2.waitKey(1) & 0xFF) in (ord("q"), 27):
                reason = "operator_quit"
                break

    if reason == "completed":
        last_t = protocol.total_ms
    if writer is not None and last_frame is not None:
        # Pad the file out to the moment the take actually ended, so the
        # container length and the cue timeline agree to the frame.
        writer.write(last_frame, last_t)

    return Take(
        end_ms=int(last_t),
        frames_captured=captured,
        frames_written=(writer.written if writer else 0),
        duplicated=(writer.duplicated if writer else 0),
        dropped=(writer.dropped if writer else 0),
        reason=reason,
    )


def measure_camera_position(
    cfg: VisionConfig,
    frames: Iterator[Tuple[int, np.ndarray]],
    *,
    window: Optional[str],
    korean: bool,
    countdown_s: float,
) -> Optional[PlacementCheckResult]:
    """Run the doc-19 camera-placement check before the take (see module docstring).

    Reuses ``pipeline_demo``'s stage machine rather than re-drawing the two cues:
    the check the operator already knows from the demo must be the same check the
    dataset records, otherwise ``camera_position`` describes a different
    measurement than the one the demo verified.
    """
    with VisionSession(cfg) as session:
        demo = PipelineDemo(cfg, session, countdown_s=countdown_s, korean=korean)
        for t_ms, frame in frames:
            view = demo.step(frame, t_ms)
            if demo.stage is Stage.INTRO:
                demo._advance(t_ms)
            if demo.stage is Stage.PLACE_RESULT:
                break
            if window is not None:
                cv2.imshow(window, view)
                if (cv2.waitKey(1) & 0xFF) in (ord("q"), 27):
                    return None
        return session.placement_result


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionPaths:
    video: Path
    cues: Path
    participant: Path
    segments: Path

    @staticmethod
    def build(out_root: Path, labels_dir: Path, pid: str, sid: str) -> "SessionPaths":
        # Session id in the cue file name, not a bare ``cues.json``: several
        # sessions of one participant share the participant directory.
        return SessionPaths(
            video=out_root / pid / f"{sid}.mp4",
            cues=out_root / pid / f"{sid}.cues.json",
            participant=out_root / pid / "participant.json",
            segments=labels_dir / f"{pid}_{sid}.segments.json",
        )


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def write_session_files(
    paths: SessionPaths,
    *,
    meta: ParticipantMeta,
    session_id: str,
    lighting: str,
    collection: CollectionConfig,
    protocol: Protocol,
    take: Take,
    cues: Sequence[Dict[str, Any]],
    placement: Optional[PlacementCheckResult],
    recorded_at: str,
    wrote_video: bool,
) -> None:
    """Persist the cue timeline and the participant metadata (doc 4-1, doc 17)."""
    payload = {
        "schema": SESSION_FILE_SCHEMA,
        "participant_id": meta.participant_id,
        "session_id": session_id,
        "recorded_at": recorded_at,
        "aborted": take.aborted,
        "stop_reason": take.reason,
        "lighting": lighting,
        "camera_position": meta.camera_position,
        "glasses": meta.glasses,
        "device_group": meta.device_group,
        "placement_check": placement.to_dict() if placement is not None else None,
        "video": _repo_relative(paths.video) if wrote_video else None,
        "record_fps": collection.record_fps,
        "frame_width": collection.frame_width,
        "frame_height": collection.frame_height,
        "countdown_s": collection.countdown_s,
        "transition_guard_ms": collection.transition_guard_ms,
        "duration_ms": take.end_ms,
        "frames_captured": take.frames_captured,
        "frames_written": take.frames_written,
        "duplicated_frames": take.duplicated,
        "dropped_frames": take.dropped,
        "blocks": [
            {
                "key": block.key,
                "seconds": block.seconds,
                "prompt": block.prompt,
                "speaking": block.speaking,
                "head_motion": block.head_motion,
            }
            for block in protocol.blocks
        ],
        "cues": list(cues),
    }
    paths.cues.parent.mkdir(parents=True, exist_ok=True)
    with paths.cues.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    sessions = []
    if paths.participant.exists():
        with paths.participant.open("r", encoding="utf-8") as handle:
            sessions = list(json.load(handle).get("sessions", []))
    if session_id not in sessions:
        sessions.append(session_id)
    with paths.participant.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema": PARTICIPANT_FILE_SCHEMA,
                "participant": meta.to_dict(),
                "lighting": lighting,
                "sessions": sessions,
                "placement_check": placement.to_dict() if placement is not None else None,
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.write("\n")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="collect",
        description=(
            "Guided recording of one take (doc 4-1): shows the cue protocol, records "
            "the video, and writes the cue timeline plus doc 4-2 segment labels."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--participant-id", help="pseudonymous id, e.g. P01 (doc 20)")
    parser.add_argument("--session-id", default="S01")
    parser.add_argument(
        "--glasses", action=argparse.BooleanOptionalAction, default=False,
        help="participant wears glasses (doc 19 bucket)",
    )
    parser.add_argument("--lighting", default="normal", choices=LIGHTING_VALUES)
    parser.add_argument(
        "--camera-position", default="auto", choices=CAMERA_POSITIONS,
        help="'auto' measures it with the doc-19 placement check before recording",
    )
    parser.add_argument("--device-group", default="laptop_internal_cam")
    parser.add_argument("--notes", default="", help="free text kept in participant.json")
    parser.add_argument(
        "--conditions", action="append", default=[],
        help="record only these protocol blocks; comma separated, repeatable",
    )
    parser.add_argument(
        "--calibration-block", action=argparse.BooleanOptionalAction, default=True,
        help="prepend the calib_camera / calib_bottom blocks doc 4-3 expects",
    )
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument(
        "--out-root", type=Path, default=None,
        help=f"default: ai/datasets/raw (dry runs: ai/datasets/raw/{DRY_RUN_SUBDIR})",
    )
    parser.add_argument("--labels-dir", type=Path, default=None, help="default: ai/datasets/labels")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing take")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="rehearse the protocol with no camera and no video file",
    )
    parser.add_argument(
        "--dry-run-fps", type=float, default=6.0,
        help="virtual frame rate of a dry run; lower is faster",
    )
    parser.add_argument("--video", type=Path, help="dry-run frame source instead of the grey card")
    parser.add_argument(
        "--save-overlay", type=Path, default=None,
        help="write one PNG per cue phase, to check the on-screen guidance headlessly",
    )
    parser.add_argument("--no-window", action="store_true", help="draw no OpenCV window")
    parser.add_argument("--english", action="store_true", help="force English on-screen text")
    parser.add_argument("--list-conditions", action="store_true", help="print the protocol and exit")
    return parser


def _print_plan(protocol: Protocol, cfg: VisionConfig) -> None:
    print(f"protocol: {len(protocol.blocks)} blocks, {protocol.total_ms / 1000:.0f}s total "
          f"(countdown {cfg.collection.countdown_s:g}s per block, "
          f"guard +/-{cfg.collection.transition_guard_ms} ms)")
    for name, window, schedule, prompt in protocol.plan_rows():
        print(f"  {name:<28} {window:>16}  {schedule:<10} {prompt}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config_dir)
    keys = [k for group in args.conditions for k in str(group).split(",")]
    protocol = build_protocol(cfg, keys=keys, with_calibration=args.calibration_block)

    if args.list_conditions:
        _print_plan(protocol, cfg)
        return 0
    if not args.participant_id:
        raise SystemExit("--participant-id is required (or use --list-conditions)")

    pid, sid = str(args.participant_id).strip(), str(args.session_id).strip()
    out_root = args.out_root or (
        DATASETS_DIR / "raw" / DRY_RUN_SUBDIR if args.dry_run else DATASETS_DIR / "raw"
    )
    labels_dir = args.labels_dir or (DATASETS_DIR / "labels")
    paths = SessionPaths.build(Path(out_root), Path(labels_dir), pid, sid)
    if paths.cues.exists() and not args.overwrite:
        raise SystemExit(f"{paths.cues} already exists; pass --overwrite to replace the take")
    if paths.video.exists() and not args.dry_run and not args.overwrite:
        raise SystemExit(f"{paths.video} already exists; pass --overwrite to replace the take")

    hud = CollectorHud(korean=not args.english)
    window = None if (args.no_window or args.dry_run) else "Pitch Coach - collect"
    overlay_dir = Path(args.save_overlay) if args.save_overlay else None
    if overlay_dir is not None:
        overlay_dir.mkdir(parents=True, exist_ok=True)

    _print_plan(protocol, cfg)
    placement: Optional[PlacementCheckResult] = None
    camera_position = args.camera_position
    source: Optional[CameraSource] = None
    writer: Optional[PacedWriter] = None

    try:
        if args.dry_run:
            size = (cfg.collection.frame_width, cfg.collection.frame_height)
            frames = synthetic_frames(size, args.dry_run_fps, args.video)
            if camera_position == "auto":
                # No camera means no measurement; recording a guess would put a
                # fabricated geometry into doc 19's bucket keys.
                camera_position = "unknown"
        else:
            source = CameraSource(
                args.camera_index, cfg.collection.frame_width, cfg.collection.frame_height,
                cfg.collection.record_fps,
            )
            print(f"camera {args.camera_index}: {source.size[0]}x{source.size[1]}")
            stream = source.frames()
            if camera_position == "auto":
                print("measuring camera placement (doc 19 webcam-position bucket)...")
                if window is not None:
                    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
                placement = measure_camera_position(
                    cfg, stream, window=window, korean=not args.english,
                    countdown_s=cfg.collection.countdown_s,
                )
                if placement is None:
                    raise SystemExit("placement check aborted; nothing was recorded")
                print(f"  {placement.summary()}")
                camera_position = _PLACEMENT_TO_POSITION[placement.placement]
                if not placement.supported:
                    print("  WARNING: unsupported geometry - CAMERA/BOTTOM may be inverted "
                          "in this take (doc 19). Recording anyway, metadata says so.")
            frames = rebase(stream)
            writer = PacedWriter(paths.video, cfg.collection.record_fps, source.size)
            if window is not None:
                cv2.namedWindow(window, cv2.WINDOW_NORMAL)

        take = record_take(
            protocol, frames, hud, writer, window=window, overlay_dir=overlay_dir,
            # A dry run renders even with no window: rehearsing the protocol
            # without exercising the guidance the participant will actually read
            # checks the half that cannot go wrong.
            render=(window is not None or overlay_dir is not None or args.dry_run),
        )
    finally:
        if writer is not None:
            writer.close()
        if source is not None:
            source.close()
        if window is not None:
            cv2.destroyAllWindows()

    meta = ParticipantMeta(
        participant_id=pid,
        glasses=bool(args.glasses),
        device_group=str(args.device_group),
        camera_position=camera_position,
        notes=str(args.notes),
    )
    cues = protocol.cue_timeline(take.end_ms, lighting=args.lighting)
    write_session_files(
        paths,
        meta=meta,
        session_id=sid,
        lighting=args.lighting,
        collection=cfg.collection,
        protocol=protocol,
        take=take,
        cues=cues,
        placement=placement,
        recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        wrote_video=writer is not None,
    )

    print(f"\n{take.reason} at {take.end_ms / 1000:.1f}s  "
          f"captured={take.frames_captured} written={take.frames_written} "
          f"duplicated={take.duplicated} dropped={take.dropped}")
    if writer is not None:
        print(f"video      -> {paths.video}")
    print(f"cues       -> {paths.cues}")
    print(f"participant-> {paths.participant}  (camera_position={camera_position})")

    if len(cues) < 2:
        print("no cue was shown; no label file written")
        return 1
    segments = segments_from_cue_timeline(
        cues, cfg.collection.transition_guard_ms, meta, sid, lighting=args.lighting
    )
    save_segments(paths.segments, segments)
    summary = segment_summary(segments)
    print(f"labels     -> {paths.segments}")
    print(f"  {summary['n_segments']} segments, {summary['total_ms'] / 1000:.1f}s, "
          f"ignore {summary['ignore_ratio'] * 100:.1f}%")
    for label, ms in sorted(summary["duration_ms_by_label"].items()):
        print(f"  {label:<8} {ms / 1000:7.1f}s")
    if overlay_dir is not None:
        print(f"overlays   -> {overlay_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
