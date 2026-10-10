"""Gaze evidence for the coach and review agents.

The agents never read frames.  They read a small, durable record of where the
presenter looked, in the shape every feature evaluator shares::

    frame decisions --GazeSlicer--> GazeSample, one per slice (1 s by default)
                    --GazeTimeline--> evaluate_gaze()       issues for the coach (now)
                                      take_summary()        statistics + segments for the review
                                      intervention_outcome() before / after a coach feedback
                                      compare_summaries()   previous-take delta

Issues use the agents' common evaluator output -- ``evaluator``,
``issue_type``, ``severity``, ``confidence``, ``persistence_sec``,
``evidence``, ``actionable`` (plus ``t_ms``) -- so a coach candidate generator
can rank gaze next to speech, script and timing.  Measurement stays here;
deciding whether to intervene stays with the coach.

Slice states are the classifier's states plus UNMEASURED (no usable face in
the slice).  Like the frontend's take statistics, every ratio divides by the
MEASURED time -- the decided states CAMERA / SCREEN / BOTTOM / OTHER -- so time
without a measurement never reads as "did not look at the audience".  An OTHER
slice carries the direction the presenter looked (``GAZE_DIRECTIONS``,
presenter-centric).

Every threshold lives in ``EvidenceConfig`` (``evidence.yaml``) and is an
initial value.  The browser port (``web/src/engine/evidence.ts``) mirrors this
module and is tested against it.

Split between device and server: frames never leave the device, so cutting
frame decisions into slices (``GazeFrame``, ``GazeSlicer``, ``decide_slice``)
and the session's ``GazeEvidenceRecorder`` live here, next to the frame
pipeline.  Everything that reads slices -- ``GazeSample``, ``GazeTimeline`` and
the issue / summary / outcome functions -- is the server core ``gaze.core`` and
is re-exported here unchanged.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from gaze.core import (
    GAZE_COACH_ACTION,
    GAZE_LOW_EYE_CONTACT,
    GAZE_UNMEASURABLE,
    RUN_ISSUES,
    SAMPLE_STATES,
    UNCERTAIN,
    UNMEASURED,
    GazeSample,
    GazeTimeline,
    Run,
    compare_summaries,
    evaluate_gaze,
    intervention_outcome,
    r4,
    take_summary,
)
from gaze_lab.config import EvidenceConfig
from gaze_lab.schemas import GAZE_DIRECTIONS, GazeDecision

__all__ = [
    "GAZE_COACH_ACTION",
    "GAZE_LOW_EYE_CONTACT",
    "GAZE_UNMEASURABLE",
    "RUN_ISSUES",
    "SAMPLE_STATES",
    "UNCERTAIN",
    "UNMEASURED",
    "GazeEvidenceRecorder",
    "GazeFrame",
    "GazeSample",
    "GazeSlicer",
    "GazeTimeline",
    "Run",
    "compare_summaries",
    "decide_slice",
    "evaluate_gaze",
    "intervention_outcome",
    "r4",
    "take_summary",
]


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
    """Frame decisions in, slices and a timeline out.  Nothing is persisted here."""

    cfg: EvidenceConfig
    timeline: GazeTimeline = field(default_factory=GazeTimeline)

    def __post_init__(self) -> None:
        self._slicer = GazeSlicer(self.cfg)

    def record(self, decision: GazeDecision, condition: Any = None) -> List[GazeSample]:
        """Add one frame decision; returns the slices it completed."""
        done = self._slicer.push(GazeFrame.from_decision(decision, condition))
        self.timeline.extend(done)
        return done

    def flush(self, t_end_ms: Optional[int] = None) -> List[GazeSample]:
        done = self._slicer.flush(t_end_ms)
        self.timeline.extend(done)
        return done

    def issues(self, t_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        end = self.timeline.end_ms
        if end is None:
            return []
        return evaluate_gaze(self.timeline, end if t_ms is None else int(t_ms), self.cfg)

    def summary(self) -> Dict[str, Any]:
        return take_summary(self.timeline, self.cfg)

    def reset(self) -> None:
        self.timeline = GazeTimeline()
        self._slicer = GazeSlicer(self.cfg)
