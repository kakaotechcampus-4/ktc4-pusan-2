"""Gaze evidence: frame decisions -> 1 s records, read by the server judgment module.

The agents never read frames.  They read a small, durable record of where the
presenter looked, one per second::

    frame decisions --GazeSlicer--> GazeSample, one per slice (1 s by default)
                    --GazeEvidenceRecorder.judgment()--> gaze.judge (the coach's 1 s judgment)
                    --GazeEvidenceRecorder.summary()---> gaze.summarize (take metrics)

Slice states are the classifier's states plus UNCERTAIN (no clear majority) and
UNMEASURED (no usable face in the slice).  An OTHER slice carries the direction
the presenter looked (``GAZE_DIRECTIONS``, presenter-centric).

The slicing thresholds live in ``EvidenceConfig`` (``evidence.yaml``).  The
browser port (``web/src/engine/evidence.ts``) mirrors the slicer and is tested
against it.

Split between device and server: frames never leave the device, so cutting
frame decisions into slices (``GazeFrame``, ``GazeSlicer``, ``decide_slice``)
and the session's ``GazeEvidenceRecorder`` live here, next to the frame
pipeline.  Reading the records -- issues, metrics, take values -- is the server
module ``gaze`` (``judge`` · ``summarize``); the recorder only calls it, the
way the coach does.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pitch_coach_ai.features.gaze.core import judge, r4, summarize
from pitch_coach_ai.features.gaze.schemas import JudgmentResult
from gaze_lab.config import EvidenceConfig
from gaze_lab.schemas import GAZE_DIRECTIONS, STATE_CLASSES, GazeDecision

__all__ = [
    "SAMPLE_STATES",
    "UNCERTAIN",
    "UNMEASURED",
    "GazeEvidenceRecorder",
    "GazeFrame",
    "GazeSample",
    "GazeSlicer",
    "decide_slice",
]

UNCERTAIN = "UNCERTAIN"
UNMEASURED = "UNMEASURED"
#: Every slice state, decided ones first (also the tie-break order of a vote).
SAMPLE_STATES: Tuple[str, ...] = tuple(STATE_CLASSES) + (UNCERTAIN, UNMEASURED)
#: How much of the take the recorder hands ``judge`` -- what the backend sends the coach.
INPUT_WINDOW_MS = 30_000


@dataclass(frozen=True)
class GazeSample:
    """One slice of gaze evidence: the 1 s record the device sends (8 fields, gaze-tracking.md 5-2)."""

    t_ms: int
    duration_ms: int
    state: str
    direction: Optional[str] = None
    #: Share of the slice's usable frames that voted for ``state`` (0 when UNMEASURED).
    confidence: float = 0.0
    #: Mean condition reliability over the slice's frames (0 for a slice without frames).
    reliability: float = 0.0
    #: Condition issues present in at least half of the slice's frames.
    issues: Tuple[str, ...] = ()
    frames: int = 0

    @property
    def end_ms(self) -> int:
        return self.t_ms + self.duration_ms

    def to_dict(self) -> Dict[str, Any]:
        return {
            "t_ms": self.t_ms,
            "duration_ms": self.duration_ms,
            "state": self.state,
            "direction": self.direction,
            "confidence": r4(self.confidence),
            "reliability": r4(self.reliability),
            "issues": list(self.issues),
            "frames": self.frames,
        }


# --------------------------------------------------------------------------
# Frames and slices
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GazeFrame:
    """One frame decision, reduced to what the evidence needs."""

    t_ms: int
    #: The decided state, or UNMEASURED when the frame had no usable face.
    state: str
    direction: Optional[str] = None
    #: Live condition reliability for this frame (1.0 when not monitored).
    reliability: float = 1.0
    #: Live condition issues for this frame.
    issues: Tuple[str, ...] = ()

    @classmethod
    def from_decision(cls, decision: GazeDecision, condition: Any = None) -> "GazeFrame":
        """From a ``GazeDecision`` and the frame's ``ConditionState`` (if any)."""
        state = decision.label if decision.face_valid else UNMEASURED
        return cls(
            t_ms=int(decision.t_ms),
            state=state,
            direction=decision.direction if state == "OTHER" else None,
            reliability=float(condition.reliability) if condition is not None else 1.0,
            issues=tuple(condition.issues) if condition is not None else (),
        )



def _mode(values: Iterable[str], order: Sequence[str]) -> Optional[str]:
    """Most common value; ties go to the earlier one in ``order``."""
    counts = Counter(values)
    if not counts:
        return None
    return max(counts, key=lambda v: (counts[v], -order.index(v) if v in order else -len(order)))


class GazeSlicer:
    """Cuts frame decisions into fixed slices on a grid anchored at the first frame.

    A slice is decided by majority of its usable frames (``slice_vote_threshold``
    of them, else UNCERTAIN); with fewer than ``min_frames_per_slice`` usable
    frames it is UNMEASURED.  A gap without frames still produces its slices,
    as UNMEASURED: an unmeasured second is recorded, not skipped.
    """

    def __init__(self, cfg: EvidenceConfig) -> None:
        self.cfg = cfg
        self._start: Optional[int] = None
        self._frames: List[GazeFrame] = []

    def push(self, frame: GazeFrame) -> List[GazeSample]:
        """Add a frame; returns the slices it completed (usually none or one)."""
        step = int(self.cfg.slice_ms)
        if self._start is None:
            self._start = int(frame.t_ms)
        out: List[GazeSample] = []
        while frame.t_ms >= self._start + step:
            out.append(self._close(self._start, step))
            self._start += step
        self._frames.append(frame)
        return out

    def flush(self, t_end_ms: Optional[int] = None) -> List[GazeSample]:
        """Close the open slice (shortened to ``t_end_ms`` when given)."""
        if self._start is None or not self._frames:
            return []
        step = int(self.cfg.slice_ms)
        end = self._start + step if t_end_ms is None else int(t_end_ms)
        duration = max(1, min(step, end - self._start))
        sample = self._close(self._start, duration)
        self._start += step
        return [sample]

    def _close(self, start: int, duration: int) -> GazeSample:
        frames, self._frames = self._frames, []
        return decide_slice(frames, start, duration, self.cfg)


def decide_slice(frames: Sequence[GazeFrame], start: int, duration: int, cfg: EvidenceConfig) -> GazeSample:
    """One slice from its frames (majority vote, see ``GazeSlicer``)."""
    reliability = sum(f.reliability for f in frames) / len(frames) if frames else 0.0
    seen: Dict[str, int] = {}
    for f in frames:
        for issue in f.issues:
            seen[issue] = seen.get(issue, 0) + 1
    issues = tuple(i for i, n in seen.items() if 2 * n >= len(frames))
    usable = [f for f in frames if f.state != UNMEASURED]
    if len(usable) < int(cfg.min_frames_per_slice):
        return GazeSample(start, duration, UNMEASURED, None, 0.0, reliability, issues, len(frames))
    winner = _mode((f.state for f in usable), SAMPLE_STATES)
    share = sum(1 for f in usable if f.state == winner) / len(usable)
    if share < float(cfg.slice_vote_threshold):
        return GazeSample(start, duration, UNCERTAIN, None, share, reliability, issues, len(frames))
    direction = None
    if winner == "OTHER":
        direction = _mode((f.direction for f in usable if f.state == "OTHER" and f.direction), GAZE_DIRECTIONS)
    return GazeSample(start, duration, winner, direction, share, reliability, issues, len(frames))


# --------------------------------------------------------------------------
# Recorder (what a session holds)
# --------------------------------------------------------------------------


@dataclass
class GazeEvidenceRecorder:
    """Frame decisions in, 1 s records out; reads them through the server module.  Nothing is persisted."""

    cfg: EvidenceConfig
    samples: List[GazeSample] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._slicer = GazeSlicer(self.cfg)

    @property
    def end_ms(self) -> Optional[int]:
        return self.samples[-1].end_ms if self.samples else None

    def record(self, decision: GazeDecision, condition: Any = None) -> List[GazeSample]:
        """Add one frame decision; returns the slices it completed."""
        done = self._slicer.push(GazeFrame.from_decision(decision, condition))
        self.samples.extend(done)
        return done

    def flush(self, t_end_ms: Optional[int] = None) -> List[GazeSample]:
        done = self._slicer.flush(t_end_ms)
        self.samples.extend(done)
        return done

    def _take_records(self, since_ms: int) -> List[Dict[str, Any]]:
        """Records ending after ``since_ms``, on the take's clock (the first record starts the take at 0).

        The session's clock also runs through set-up and calibration; the frontend hands the engine
        take-relative times (gaze-tracking.md 5-6), so the server reads the take from 0.
        """
        t0 = self.samples[0].t_ms
        out = []
        for s in self.samples:
            if s.end_ms - t0 > since_ms:
                out.append({**s.to_dict(), "t_ms": s.t_ms - t0})
        return out

    def judgment(
        self, t_ms: Optional[int] = None, voiced: Optional[List[Dict[str, int]]] = None
    ) -> Optional[JudgmentResult]:
        """The coach's 1 s judgment at take time ``t_ms`` (default: the end of the last record).

        None before the first record.  Like the backend, it hands ``gaze.judge`` only the last 30 s
        of records; ``voiced`` is the same seconds' speaking time (``[{t_ms, voiced_ms}]``, take
        time, none in the demo).
        """
        end = self.end_ms
        if end is None:
            return None
        t = end - self.samples[0].t_ms if t_ms is None else int(t_ms)
        records = self._take_records(t - INPUT_WINDOW_MS)
        inputs = {"t_ms": t, "since_ms": max(0, t - 1000), "records": records, "voiced": voiced or []}
        [result] = judge(inputs, t)
        return result

    def issues(self, t_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        """The gaze issues of ``judgment`` as plain dicts (empty before the first record)."""
        result = self.judgment(t_ms)
        return [] if result is None else [i.model_dump(mode="json") for i in result.issues]

    def summary(self, voiced: Optional[List[Dict[str, int]]] = None) -> Dict[str, Any]:
        """The take's gaze metrics: every record judged once, the tally added up, then ``gaze.summarize``."""
        end = self.end_ms
        if end is None:
            return summarize({})
        t = end - self.samples[0].t_ms
        records = self._take_records(0)
        [result] = judge({"t_ms": t, "since_ms": 0, "records": records, "voiced": voiced or []}, t)
        total: Dict[str, float] = {}
        for item in result.tally:
            for k, v in item.values.items():
                total[k] = total.get(k, 0) + v
        return summarize({"GAZE": total})

    def reset(self) -> None:
        self.samples = []
        self._slicer = GazeSlicer(self.cfg)
