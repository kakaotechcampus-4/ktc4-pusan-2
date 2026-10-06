"""1초 기록 → 코치 이슈 · 테이크 요약 · 이전 테이크 비교 · 개입 효과 (서버 코어).

브라우저의 시선 엔진이 프레임 판정을 1초 기록(``GazeSample``)으로 모아 보내면,
서버는 이 모듈로 코치 · 리뷰 에이전트가 읽을 값을 만든다. 프레임은 기기를 떠나지 않으므로
서버가 받는 첫 데이터가 1초 기록이다::

    1초 기록 --GazeTimeline--> evaluate_gaze()         코치: 지금의 이슈
                               take_summary()          리뷰: 통계 · 구간 · 문제 구간
                               compare_summaries()     리뷰: 이전 테이크와의 차이
                               intervention_outcome()  코치: 피드백 전후 효과

이슈는 에이전트 공통 평가기 형식이다 -- ``evaluator``, ``issue_type``, ``severity``,
``confidence``, ``persistence_sec``, ``evidence``, ``actionable`` (+ ``t_ms``).
측정은 여기서 끝나고, 끼어들지 말지는 코치가 정한다.

1초 기록의 상태는 판정된 4개(CAMERA / SCREEN / BOTTOM / OTHER)와 UNCERTAIN(얼굴은 있으나 판정 보류),
UNMEASURED(얼굴 없음)다. 비율은 모두 **측정된 시간**(판정된 4개 상태)으로 나눈다 -- 재지 못한 시간이
"청중을 안 봤다"로 읽히지 않게 하기 위해서다. OTHER 기록에는 발표자 기준 방향(``GAZE_DIRECTIONS``)이 붙는다.

임계값은 모두 ``EvidenceConfig``(research 의 configs/evidence.yaml 과 같은 값)에 있다.
프레임 → 1초 기록(GazeSlicer)은 기기 쪽이라 이 모듈에 없다 (브라우저 엔진 evidence.ts,
Python 기준 구현 gaze_lab.evidence.gaze).

원본: ai/archive/workspaces/jewon-kim/gaze-tracking/v1/local/ai/src/vision/evidence/gaze.py
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .config import EvidenceConfig

#: Every state the classifier can decide, in tie-break order (gaze_lab.schemas.STATE_CLASSES).
STATE_CLASSES: tuple[str, ...] = ("CAMERA", "SCREEN", "BOTTOM", "OTHER")

#: Presenter-centric direction of an OTHER look (gaze_lab.schemas.GAZE_DIRECTIONS).
GAZE_DIRECTIONS: tuple[str, ...] = (
    "RIGHT",
    "UP_RIGHT",
    "UP",
    "UP_LEFT",
    "LEFT",
    "DOWN_LEFT",
    "DOWN",
    "DOWN_RIGHT",
)

UNCERTAIN = "UNCERTAIN"
UNMEASURED = "UNMEASURED"
#: Every slice state, decided ones first (also the tie-break order of a vote).
SAMPLE_STATES: tuple[str, ...] = tuple(STATE_CLASSES) + (UNCERTAIN, UNMEASURED)

#: Continuous-run issues: state -> issue type.
RUN_ISSUES: dict[str, str] = {
    "BOTTOM": "GAZE_ON_SCRIPT",
    "SCREEN": "GAZE_ON_SCREEN",
    "OTHER": "GAZE_AWAY",
}
GAZE_LOW_EYE_CONTACT = "GAZE_LOW_EYE_CONTACT"
GAZE_UNMEASURABLE = "GAZE_UNMEASURABLE"

#: The coach action each gaze issue asks for (the agent may still choose WAIT).
#: GAZE_UNMEASURABLE has none: it tells the coach not to give gaze feedback.
GAZE_COACH_ACTION: dict[str, str] = {
    "GAZE_ON_SCRIPT": "LOOK_AT_CAMERA",
    "GAZE_ON_SCREEN": "LOOK_AT_CAMERA",
    "GAZE_AWAY": "LOOK_AT_CAMERA",
    GAZE_LOW_EYE_CONTACT: "LOOK_AT_CAMERA",
}

#: The ratio an intervention should move, and which way (+1 up, -1 down).
_OUTCOME_TARGET: dict[str, tuple[str, int]] = {
    "GAZE_ON_SCRIPT": ("BOTTOM", -1),
    "GAZE_ON_SCREEN": ("SCREEN", -1),
    "GAZE_AWAY": ("OTHER", -1),
    GAZE_LOW_EYE_CONTACT: ("CAMERA", +1),
}


def r4(value: float | None) -> float | None:
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
# Slices (the wire record)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GazeSample:
    """One slice of gaze evidence (the unit a timeline, and a wire record, is made of)."""

    t_ms: int
    duration_ms: int
    state: str
    direction: str | None = None
    #: Share of the slice's usable frames that voted for ``state`` (0 when UNMEASURED).
    confidence: float = 0.0
    #: Mean condition reliability over the slice's frames (0 for a slice without frames).
    reliability: float = 0.0
    #: Condition issues present in at least half of the slice's frames.
    issues: tuple[str, ...] = ()
    frames: int = 0

    @property
    def end_ms(self) -> int:
        return self.t_ms + self.duration_ms

    def to_dict(self) -> dict[str, Any]:
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
    def from_dict(cls, d: dict[str, Any]) -> GazeSample:
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


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------


@dataclass
class Run:
    """Consecutive slices of one state."""

    state: str
    start_ms: int
    end_ms: int
    direction: str | None = None
    mean_confidence: float = 0.0
    mean_reliability: float = 0.0
    samples: int = 0

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
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
        self.samples: list[GazeSample] = list(samples)

    def append(self, sample: GazeSample) -> None:
        self.samples.append(sample)

    def extend(self, samples: Iterable[GazeSample]) -> None:
        self.samples.extend(samples)

    @property
    def start_ms(self) -> int | None:
        return self.samples[0].t_ms if self.samples else None

    @property
    def end_ms(self) -> int | None:
        return self.samples[-1].end_ms if self.samples else None

    def until(self, t_ms: int) -> GazeTimeline:
        """The timeline as it stood at ``t_ms`` (later slices dropped, the last one clipped)."""
        out: list[GazeSample] = []
        for s in self.samples:
            if s.t_ms >= t_ms:
                break
            if s.end_ms > t_ms:
                s = GazeSample(
                    s.t_ms,
                    t_ms - s.t_ms,
                    s.state,
                    s.direction,
                    s.confidence,
                    s.reliability,
                    s.issues,
                    s.frames,
                )
            out.append(s)
        return GazeTimeline(out)

    def stats(self, t_end_ms: int, window_ms: int) -> dict[str, Any]:
        """Time spent per state over ``[t_end - window, t_end)``; ratios over measured time."""
        t0 = int(t_end_ms) - int(window_ms)
        state_ms = {s: 0 for s in SAMPLE_STATES}
        direction_ms = {d: 0 for d in GAZE_DIRECTIONS}
        issue_ms: dict[str, int] = {}
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

    def runs(self, min_ms: int = 0) -> list[Run]:
        """Consecutive same-state slices (OTHER merges across directions)."""
        out: list[Run] = []
        current: list[GazeSample] = []

        def close() -> None:
            if not current:
                return
            total = sum(s.duration_ms for s in current)
            direction = None
            if current[0].state == "OTHER":
                weights: dict[str, int] = {}
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
                mean_confidence=sum(s.confidence * s.duration_ms for s in current) / total
                if total
                else 0.0,
                mean_reliability=sum(s.reliability * s.duration_ms for s in current) / total
                if total
                else 0.0,
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


def _issue(
    issue_type: str,
    t_ms: int,
    severity: float,
    confidence: float,
    persistence_ms: float,
    evidence: dict[str, Any],
    actionable: bool,
) -> dict[str, Any]:
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


def evaluate_gaze(timeline: GazeTimeline, t_ms: int, cfg: EvidenceConfig) -> list[dict[str, Any]]:
    """Gaze issues at ``t_ms``, most severe first, in the agents' evaluator format."""
    tl = timeline.until(int(t_ms))
    if not tl.samples:
        return []
    short_s, long_s = _secs(cfg.short_window_ms), _secs(cfg.long_window_ms)
    short = tl.stats(t_ms, cfg.short_window_ms)
    long = tl.stats(t_ms, cfg.long_window_ms)
    issues: list[dict[str, Any]] = []

    runs = tl.runs()
    run = runs[-1] if runs else None
    limits = {
        "BOTTOM": (cfg.script_min_ms, cfg.script_full_ms),
        "SCREEN": (cfg.screen_min_ms, cfg.screen_full_ms),
        "OTHER": (cfg.away_min_ms, cfg.away_full_ms),
    }
    if run is not None and run.state in RUN_ISSUES and run.duration_ms >= limits[run.state][0]:
        key = run.state.lower()
        evidence: dict[str, Any] = {
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
        issues.append(
            _issue(
                RUN_ISSUES[run.state],
                t_ms,
                severity=run.duration_ms / float(limits[run.state][1]),
                confidence=run.mean_confidence * run.mean_reliability,
                persistence_ms=run.duration_ms,
                evidence=evidence,
                actionable=True,
            )
        )

    camera = long["ratio"]["CAMERA"]
    threshold = float(cfg.low_eye_contact_ratio)
    if (
        long["measured_ms"] >= cfg.low_eye_contact_min_measured_ms
        and camera is not None
        and camera < threshold
    ):
        issues.append(
            _issue(
                GAZE_LOW_EYE_CONTACT,
                t_ms,
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
            )
        )

    cov_thr, rel_thr = float(cfg.unmeasurable_coverage), float(cfg.unmeasurable_reliability)
    coverage, reliability = short["coverage"], short["mean_reliability"]
    if short["tracked_ms"] > 0 and (coverage < cov_thr or reliability < rel_thr):
        worst = min(
            coverage / cov_thr if cov_thr > 0 else 1.0,
            reliability / rel_thr if rel_thr > 0 else 1.0,
        )
        issues.append(
            _issue(
                GAZE_UNMEASURABLE,
                t_ms,
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
            )
        )

    order = {id(i): n for n, i in enumerate(issues)}
    issues.sort(key=lambda i: (-i["severity"], order[id(i)]))
    return issues


# --------------------------------------------------------------------------
# Review: the whole take
# --------------------------------------------------------------------------


def take_summary(timeline: GazeTimeline, cfg: EvidenceConfig) -> dict[str, Any]:
    """Gaze statistics, segments and problem segments of a whole take."""
    if not timeline.samples:
        return {
            "evaluator": "gaze",
            "tracked_ms": 0,
            "measured_ms": 0,
            "segments": [],
            "problem_segments": [],
        }
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
    longest = {
        s: max((r.duration_ms for r in runs if r.state == s), default=0) for s in STATE_CLASSES
    }
    episodes = {
        s: sum(1 for r in runs if r.state == s and r.duration_ms >= cfg.segment_min_ms)
        for s in STATE_CLASSES
    }
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


def compare_summaries(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Previous-take delta: the same numbers side by side, ``delta = current - previous``."""

    def pair(a: float | None, b: float | None) -> dict[str, float | None]:
        delta = None if a is None or b is None else r4(b - a)
        return {"previous": a, "current": b, "delta": delta}

    return {
        "eye_contact_ratio": pair(
            previous.get("eye_contact_ratio"), current.get("eye_contact_ratio")
        ),
        "coverage": pair(previous.get("coverage"), current.get("coverage")),
        "mean_reliability": pair(previous.get("mean_reliability"), current.get("mean_reliability")),
        "state_ratio": {
            s: pair(
                (previous.get("state_ratio") or {}).get(s),
                (current.get("state_ratio") or {}).get(s),
            )
            for s in STATE_CLASSES
        },
        "episodes": {
            s: pair((previous.get("episodes") or {}).get(s), (current.get("episodes") or {}).get(s))
            for s in STATE_CLASSES
        },
        "longest_run_ms": {
            s: pair(
                (previous.get("longest_run_ms") or {}).get(s),
                (current.get("longest_run_ms") or {}).get(s),
            )
            for s in STATE_CLASSES
        },
    }


def intervention_outcome(
    timeline: GazeTimeline, t_ms: int, issue_type: str, cfg: EvidenceConfig
) -> dict[str, Any]:
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
    effective = (
        None if rb is None or ra is None else (ra - rb) * sign >= float(cfg.outcome_min_change)
    )
    return {
        "intervention": {
            "type": GAZE_COACH_ACTION[issue_type],
            "issue_type": issue_type,
            "t_ms": int(t_ms),
        },
        "before": {
            f"{key}_ratio_{_secs(cfg.outcome_before_ms)}s": r4(rb),
            "measured_ms": before["measured_ms"],
        },
        f"after_{_secs(cfg.outcome_delay_ms)}s": {
            f"{key}_ratio_{_secs(cfg.outcome_after_ms)}s": r4(ra),
            "measured_ms": after["measured_ms"],
        },
        "effective": effective,
    }
