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
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from gaze_lab.config import EvidenceConfig
from gaze_lab.schemas import GAZE_DIRECTIONS, STATE_CLASSES, GazeDecision

UNCERTAIN = "UNCERTAIN"
UNMEASURED = "UNMEASURED"
#: Every slice state, decided ones first (also the tie-break order of a vote).
SAMPLE_STATES: Tuple[str, ...] = tuple(STATE_CLASSES) + (UNCERTAIN, UNMEASURED)

#: Continuous-run issues: state -> issue type.
RUN_ISSUES: Dict[str, str] = {
    "BOTTOM": "GAZE_ON_SCRIPT",
    "SCREEN": "GAZE_ON_SCREEN",
    "OTHER": "GAZE_AWAY",
}
GAZE_LOW_EYE_CONTACT = "GAZE_LOW_EYE_CONTACT"
GAZE_UNMEASURABLE = "GAZE_UNMEASURABLE"

#: The coach action each gaze issue asks for (the agent may still choose WAIT).
#: GAZE_UNMEASURABLE has none: it tells the coach not to give gaze feedback.
GAZE_COACH_ACTION: Dict[str, str] = {
    "GAZE_ON_SCRIPT": "LOOK_AT_CAMERA",
    "GAZE_ON_SCREEN": "LOOK_AT_CAMERA",
    "GAZE_AWAY": "LOOK_AT_CAMERA",
    GAZE_LOW_EYE_CONTACT: "LOOK_AT_CAMERA",
}

#: The ratio an intervention should move, and which way (+1 up, -1 down).
_OUTCOME_TARGET: Dict[str, Tuple[str, int]] = {
    "GAZE_ON_SCRIPT": ("BOTTOM", -1),
    "GAZE_ON_SCREEN": ("SCREEN", -1),
    "GAZE_AWAY": ("OTHER", -1),
    GAZE_LOW_EYE_CONTACT: ("CAMERA", +1),
}


def r4(value: Optional[float]) -> Optional[float]:
    """Round half up to 4 decimals -- the same arithmetic in the TypeScript port."""
    if value is None:
        return None
    return math.floor(float(value) * 10000.0 + 0.5) / 10000.0


def _secs(ms: int) -> str:
    """5000 -> "5", 2500 -> "2.5" (window suffix in evidence keys)."""
    return f"{ms / 1000.0:g}"


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else float(value)


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


@dataclass(frozen=True)
class GazeSample:
    """One slice of gaze evidence (the unit a timeline, and a wire record, is made of)."""

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

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GazeSample":
        return cls(
            t_ms=int(d["t_ms"]),
            duration_ms=int(d["duration_ms"]),
            state=str(d["state"]),
            direction=d.get("direction"),
            confidence=float(d.get("confidence", 0.0)),
            reliability=float(d.get("reliability", 0.0)),
            issues=tuple(d.get("issues") or ()),
            frames=int(d.get("frames", 0)),
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
# Timeline
# --------------------------------------------------------------------------


@dataclass
class Run:
    """Consecutive slices of one state."""

    state: str
    start_ms: int
    end_ms: int
    direction: Optional[str] = None
    mean_confidence: float = 0.0
    mean_reliability: float = 0.0
    samples: int = 0

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "duration_ms": self.duration_ms,
            "direction": self.direction,
            "mean_confidence": r4(self.mean_confidence),
            "mean_reliability": r4(self.mean_reliability),
        }


class GazeTimeline:
    """Slices in time order, read through windows and runs."""

    def __init__(self, samples: Iterable[GazeSample] = ()) -> None:
        self.samples: List[GazeSample] = list(samples)

    def append(self, sample: GazeSample) -> None:
        self.samples.append(sample)

    def extend(self, samples: Iterable[GazeSample]) -> None:
        self.samples.extend(samples)

    @property
    def start_ms(self) -> Optional[int]:
        return self.samples[0].t_ms if self.samples else None

    @property
    def end_ms(self) -> Optional[int]:
        return self.samples[-1].end_ms if self.samples else None

    def until(self, t_ms: int) -> "GazeTimeline":
        """The timeline as it stood at ``t_ms`` (later slices dropped, the last one clipped)."""
        out: List[GazeSample] = []
        for s in self.samples:
            if s.t_ms >= t_ms:
                break
            if s.end_ms > t_ms:
                s = GazeSample(s.t_ms, t_ms - s.t_ms, s.state, s.direction, s.confidence,
                               s.reliability, s.issues, s.frames)
            out.append(s)
        return GazeTimeline(out)

    def stats(self, t_end_ms: int, window_ms: int) -> Dict[str, Any]:
        """Time spent per state over ``[t_end - window, t_end)``; ratios over measured time."""
        t0 = int(t_end_ms) - int(window_ms)
        state_ms = {s: 0 for s in SAMPLE_STATES}
        direction_ms = {d: 0 for d in GAZE_DIRECTIONS}
        issue_ms: Dict[str, int] = {}
        rel_sum = 0.0
        conf_sum = 0.0
        for s in self.samples:
            overlap = min(s.end_ms, int(t_end_ms)) - max(s.t_ms, t0)
            if overlap <= 0:
                continue
            state_ms[s.state] += overlap
            rel_sum += s.reliability * overlap
            if s.state in STATE_CLASSES:
                conf_sum += s.confidence * overlap
            if s.state == "OTHER" and s.direction in direction_ms:
                direction_ms[s.direction] += overlap
            for issue in s.issues:
                issue_ms[issue] = issue_ms.get(issue, 0) + overlap
        measured = sum(state_ms[s] for s in STATE_CLASSES)
        tracked = sum(state_ms.values())
        return {
            "t_start_ms": t0,
            "t_end_ms": int(t_end_ms),
            "window_ms": int(window_ms),
            "tracked_ms": tracked,
            "measured_ms": measured,
            "uncertain_ms": state_ms[UNCERTAIN],
            "unmeasured_ms": state_ms[UNMEASURED],
            "coverage": measured / tracked if tracked else 0.0,
            "state_ms": state_ms,
            "ratio": {s: (state_ms[s] / measured if measured else None) for s in STATE_CLASSES},
            "other_direction_ms": direction_ms,
            "issue_ms": issue_ms,
            "mean_reliability": rel_sum / tracked if tracked else 0.0,
            "mean_confidence": conf_sum / measured if measured else 0.0,
        }

    def runs(self, min_ms: int = 0) -> List[Run]:
        """Consecutive same-state slices (OTHER merges across directions)."""
        out: List[Run] = []
        current: List[GazeSample] = []

        def close() -> None:
            if not current:
                return
            total = sum(s.duration_ms for s in current)
            direction = None
            if current[0].state == "OTHER":
                weights: Dict[str, int] = {}
                for s in current:
                    if s.direction:
                        weights[s.direction] = weights.get(s.direction, 0) + s.duration_ms
                if weights:
                    direction = max(weights, key=lambda d: (weights[d], -GAZE_DIRECTIONS.index(d)))
            run = Run(
                state=current[0].state,
                start_ms=current[0].t_ms,
                end_ms=current[-1].end_ms,
                direction=direction,
                mean_confidence=sum(s.confidence * s.duration_ms for s in current) / total if total else 0.0,
                mean_reliability=sum(s.reliability * s.duration_ms for s in current) / total if total else 0.0,
                samples=len(current),
            )
            if run.duration_ms >= min_ms:
                out.append(run)

        for s in self.samples:
            if current and (s.state != current[-1].state or s.t_ms != current[-1].end_ms):
                close()
                current = []
            current.append(s)
        close()
        return out


# --------------------------------------------------------------------------
# Coach: issues now
# --------------------------------------------------------------------------


def _issue(issue_type: str, t_ms: int, severity: float, confidence: float, persistence_ms: float,
           evidence: Dict[str, Any], actionable: bool) -> Dict[str, Any]:
    return {
        "evaluator": "gaze",
        "issue_type": issue_type,
        "t_ms": int(t_ms),
        "severity": r4(_clamp01(severity)),
        "confidence": r4(_clamp01(confidence)),
        "persistence_sec": math.floor(persistence_ms / 10.0 + 0.5) / 100.0,
        "evidence": evidence,
        "actionable": bool(actionable),
    }


def evaluate_gaze(timeline: GazeTimeline, t_ms: int, cfg: EvidenceConfig) -> List[Dict[str, Any]]:
    """Gaze issues at ``t_ms``, most severe first, in the agents' evaluator format."""
    tl = timeline.until(int(t_ms))
    if not tl.samples:
        return []
    short_s, long_s = _secs(cfg.short_window_ms), _secs(cfg.long_window_ms)
    short = tl.stats(t_ms, cfg.short_window_ms)
    long = tl.stats(t_ms, cfg.long_window_ms)
    issues: List[Dict[str, Any]] = []

    runs = tl.runs()
    run = runs[-1] if runs else None
    limits = {
        "BOTTOM": (cfg.script_min_ms, cfg.script_full_ms),
        "SCREEN": (cfg.screen_min_ms, cfg.screen_full_ms),
        "OTHER": (cfg.away_min_ms, cfg.away_full_ms),
    }
    if run is not None and run.state in RUN_ISSUES and run.duration_ms >= limits[run.state][0]:
        key = run.state.lower()
        evidence: Dict[str, Any] = {
            "state": run.state,
            "run_start_ms": run.start_ms,
            "continuous_ms": run.duration_ms,
            f"{key}_ratio_{short_s}s": r4(short["ratio"][run.state]),
            f"{key}_ratio_{long_s}s": r4(long["ratio"][run.state]),
            f"camera_ratio_{long_s}s": r4(long["ratio"]["CAMERA"]),
            "mean_reliability": r4(run.mean_reliability),
        }
        if run.state == "OTHER":
            evidence["direction"] = run.direction
        issues.append(_issue(
            RUN_ISSUES[run.state], t_ms,
            severity=run.duration_ms / float(limits[run.state][1]),
            confidence=run.mean_confidence * run.mean_reliability,
            persistence_ms=run.duration_ms,
            evidence=evidence,
            actionable=True,
        ))

    camera = long["ratio"]["CAMERA"]
    threshold = float(cfg.low_eye_contact_ratio)
    if long["measured_ms"] >= cfg.low_eye_contact_min_measured_ms and camera is not None and camera < threshold:
        issues.append(_issue(
            GAZE_LOW_EYE_CONTACT, t_ms,
            severity=(threshold - camera) / threshold if threshold > 0 else 0.0,
            confidence=long["mean_confidence"] * long["mean_reliability"],
            persistence_ms=long["measured_ms"],
            evidence={
                f"camera_ratio_{long_s}s": r4(camera),
                f"screen_ratio_{long_s}s": r4(long["ratio"]["SCREEN"]),
                f"bottom_ratio_{long_s}s": r4(long["ratio"]["BOTTOM"]),
                f"other_ratio_{long_s}s": r4(long["ratio"]["OTHER"]),
                "measured_ms": long["measured_ms"],
            },
            actionable=True,
        ))

    cov_thr, rel_thr = float(cfg.unmeasurable_coverage), float(cfg.unmeasurable_reliability)
    coverage, reliability = short["coverage"], short["mean_reliability"]
    if short["tracked_ms"] > 0 and (coverage < cov_thr or reliability < rel_thr):
        worst = min(coverage / cov_thr if cov_thr > 0 else 1.0, reliability / rel_thr if rel_thr > 0 else 1.0)
        issues.append(_issue(
            GAZE_UNMEASURABLE, t_ms,
            severity=1.0 - worst,
            confidence=1.0,
            persistence_ms=short["tracked_ms"] - short["measured_ms"],
            evidence={
                f"coverage_{short_s}s": r4(coverage),
                f"mean_reliability_{short_s}s": r4(reliability),
                "unmeasured_ms": short["unmeasured_ms"],
                "uncertain_ms": short["uncertain_ms"],
                "condition_issue_ms": dict(short["issue_ms"]),
            },
            actionable=False,
        ))

    order = {id(i): n for n, i in enumerate(issues)}
    issues.sort(key=lambda i: (-i["severity"], order[id(i)]))
    return issues


# --------------------------------------------------------------------------
# Review: the whole take
# --------------------------------------------------------------------------


def take_summary(timeline: GazeTimeline, cfg: EvidenceConfig) -> Dict[str, Any]:
    """Gaze statistics, segments and problem segments of a whole take."""
    if not timeline.samples:
        return {"evaluator": "gaze", "tracked_ms": 0, "measured_ms": 0, "segments": [], "problem_segments": []}
    start, end = timeline.start_ms, timeline.end_ms
    total = timeline.stats(end, end - start)
    runs = timeline.runs()
    limits = {"BOTTOM": cfg.script_min_ms, "SCREEN": cfg.screen_min_ms, "OTHER": cfg.away_min_ms}
    segments = [r.to_dict() for r in runs if r.duration_ms >= cfg.segment_min_ms]
    problems = []
    for r in runs:
        if r.state in RUN_ISSUES and r.duration_ms >= limits[r.state]:
            entry = r.to_dict()
            entry["issue_type"] = RUN_ISSUES[r.state]
            problems.append(entry)
    longest = {s: max((r.duration_ms for r in runs if r.state == s), default=0) for s in STATE_CLASSES}
    episodes = {s: sum(1 for r in runs if r.state == s and r.duration_ms >= cfg.segment_min_ms) for s in STATE_CLASSES}
    return {
        "evaluator": "gaze",
        "start_ms": start,
        "end_ms": end,
        "tracked_ms": total["tracked_ms"],
        "measured_ms": total["measured_ms"],
        "uncertain_ms": total["uncertain_ms"],
        "unmeasured_ms": total["unmeasured_ms"],
        "coverage": r4(total["coverage"]),
        "state_ms": {s: total["state_ms"][s] for s in SAMPLE_STATES},
        "state_ratio": {s: r4(v) for s, v in total["ratio"].items()},
        "eye_contact_ratio": r4(total["ratio"]["CAMERA"]),
        "other_direction_ms": dict(total["other_direction_ms"]),
        "mean_reliability": r4(total["mean_reliability"]),
        "condition_issue_ms": dict(total["issue_ms"]),
        "longest_run_ms": longest,
        "episodes": episodes,
        "segments": segments,
        "problem_segments": problems,
    }


def compare_summaries(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """Previous-take delta: the same numbers side by side, ``delta = current - previous``."""

    def pair(a: Optional[float], b: Optional[float]) -> Dict[str, Optional[float]]:
        delta = None if a is None or b is None else r4(b - a)
        return {"previous": a, "current": b, "delta": delta}

    return {
        "eye_contact_ratio": pair(previous.get("eye_contact_ratio"), current.get("eye_contact_ratio")),
        "coverage": pair(previous.get("coverage"), current.get("coverage")),
        "mean_reliability": pair(previous.get("mean_reliability"), current.get("mean_reliability")),
        "state_ratio": {
            s: pair((previous.get("state_ratio") or {}).get(s), (current.get("state_ratio") or {}).get(s))
            for s in STATE_CLASSES
        },
        "episodes": {
            s: pair((previous.get("episodes") or {}).get(s), (current.get("episodes") or {}).get(s))
            for s in STATE_CLASSES
        },
        "longest_run_ms": {
            s: pair((previous.get("longest_run_ms") or {}).get(s), (current.get("longest_run_ms") or {}).get(s))
            for s in STATE_CLASSES
        },
    }


def intervention_outcome(timeline: GazeTimeline, t_ms: int, issue_type: str, cfg: EvidenceConfig) -> Dict[str, Any]:
    """Did the presenter change after a coach feedback for ``issue_type`` at ``t_ms``?

    The target ratio over ``outcome_before_ms`` before the feedback against the
    window ``outcome_delay_ms`` after it; ``effective`` is ``None`` while either
    window has no measured time (still waiting, or nothing to judge).
    """
    if issue_type not in _OUTCOME_TARGET:
        raise ValueError(f"no outcome rule for {issue_type!r}; known: {sorted(_OUTCOME_TARGET)}")
    state, sign = _OUTCOME_TARGET[issue_type]
    before = timeline.stats(int(t_ms), cfg.outcome_before_ms)
    after_end = int(t_ms) + int(cfg.outcome_delay_ms) + int(cfg.outcome_after_ms)
    after = timeline.stats(after_end, cfg.outcome_after_ms)
    rb, ra = before["ratio"][state], after["ratio"][state]
    key = state.lower()
    effective = None if rb is None or ra is None else (ra - rb) * sign >= float(cfg.outcome_min_change)
    return {
        "intervention": {"type": GAZE_COACH_ACTION[issue_type], "issue_type": issue_type, "t_ms": int(t_ms)},
        "before": {f"{key}_ratio_{_secs(cfg.outcome_before_ms)}s": r4(rb), "measured_ms": before["measured_ms"]},
        f"after_{_secs(cfg.outcome_delay_ms)}s": {
            f"{key}_ratio_{_secs(cfg.outcome_after_ms)}s": r4(ra),
            "measured_ms": after["measured_ms"],
        },
        "effective": effective,
    }


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
