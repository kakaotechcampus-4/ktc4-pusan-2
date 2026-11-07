"""가상 기록 만들기 — 피치 · Take · 코치 Take 결과 · 리뷰 · 대본 전달도 (연구 전용).

- 누가 쓰나: 예시 JSON 만들기(example_builder.py) · 서비스 단위 시험, 그리고 연구 모듈의 가짜 BE(fake_be.py) ·
  평가 시나리오 실행기(evals.py · scenario.py).
- 연구 모듈도 쓴다: 연구 `chat_agent_lab.service_tests`가 이 파일을 실행 코드(가짜 BE · 평가 · 예시 명령)에서 가져다 쓴다
  (기능 문서 4-1 · 10-2). 이름 · 모양을 바꾸면 연구 모듈 시험(`ai/research/chat-agent`에서 pytest)도 돌린다.
- 모양은 실제 저장 payload 와 같다: 코치 Take 결과는 서비스 `features/coach/examples/finalize/response.json`의
  `take_result`, 리뷰는 `features/review/examples/review/response.json`. 숫자는 가상이고 실제 사용자 기록이 아니다
  (기능 문서 13-1: 실제 사용자 기록은 동의 없이 쓰지 않는다).
- 처리: `take_result(...)`로 영역별 Take · 장 값과 문제 구간을 만들고, `review(...)`로 차원 상태 · 점수를 만들고,
  `TakeRecord` · `PitchRecord`로 묶는다. BE가 '끝까지 한 발표'(`completed_fully`)를 계산하는 규칙(12-3)은
  `completed_fully()`에 있다.
- 고칠 때: 코치 · 리뷰 결과 모양이 바뀌면 위 두 예시 파일과 같이 맞춘다. 판정 기준 버전 문자열은 아무 값이어도 되지만
  Take끼리 비교할 때 같아야 비교된다(6-4).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

#: 영역별 판정 기준 버전(가상). 바꾸면 그 영역 지표는 비교 · 목표 판정이 막힌다(6-4)
CRITERIA_VERSIONS = {
    "GAZE": "gaze-1.3+1c10b82348a2",
    "SPEED": "pace-1.0+b24c6ce0efd4",
    "VOLUME": "volume-1.1+19f685ae6d67",
    "PAUSE": "volume-1.1+19f685ae6d67",
    "FILLER": "filler-1.0+3e62c0c87d2a",
    "TIME": "timing-1.1+305f8e44237d",
}
#: 말 속도 판정 기준(가상, 코치 예시와 같은 값): 이보다 빠르면 PACE_FAST, 느리면 PACE_SLOW
PACE_FAST_CPM = 350.0
PACE_SLOW_CPM = 275.0


def _criteria(versions: dict[str, str] | None = None) -> dict[str, Any]:
    v = {**CRITERIA_VERSIONS, **(versions or {})}
    return {
        "GAZE": {"criteria_version": v["GAZE"], "issues": {}},
        "SPEED": {
            "criteria_version": v["SPEED"],
            "issues": {
                "PACE_FAST": {
                    "metric": "cpm",
                    "direction": "HIGHER_IS_WORSE",
                    "threshold": PACE_FAST_CPM,
                },
                "PACE_SLOW": {
                    "metric": "cpm",
                    "direction": "LOWER_IS_WORSE",
                    "threshold": PACE_SLOW_CPM,
                },
            },
        },
        "VOLUME": {"criteria_version": v["VOLUME"], "issues": {}},
        "PAUSE": {"criteria_version": v["PAUSE"], "issues": {}},
        "FILLER": {"criteria_version": v["FILLER"], "issues": {}},
        "TIME": {"criteria_version": v["TIME"], "issues": {}},
    }


def take_result(
    *,
    take_id: str,
    slides: int = 4,
    slide_ms: int = 60_000,
    audience: float | list[float] = 0.6,
    script_ratio: float | list[float] = 0.2,
    cpm: float | list[float] = 310.0,
    filler_per_min: float = 1.2,
    filler_count: int = 5,
    voice_diff_db: float = -0.5,
    long_silence_count: int = 0,
    issues: list[tuple[str, int | None, bool]] | None = None,
    unmeasured: dict[str, str] | None = None,
    script_mode: str = "OFF",
    plan: dict[str, int] | None = None,
    criteria_versions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """코치 Take 결과 하나. 리스트로 준 값은 장마다, 수 하나면 모든 장에 같은 값.

    - issues: (문제 이름, 장 번호 · None, reliable) 목록. 예: [("PACE_FAST", 2, True)]
    - unmeasured: {영역: 사유}. 그 영역은 take 값이 null 이고 unmeasured_reason 이 붙는다
    """

    def per_slide(v: float | list[float], i: int) -> float:
        return v[i] if isinstance(v, list) else v

    n = slides
    duration = n * slide_ms
    aud = [round(per_slide(audience, i), 4) for i in range(n)]
    scr = [round(per_slide(script_ratio, i), 4) for i in range(n)]
    sp = [round(per_slide(cpm, i), 1) for i in range(n)]

    def slide_rows(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "slide_number": i + 1,
                "start_ms": i * slide_ms,
                "end_ms": (i + 1) * slide_ms,
                "target_ms": slide_ms,
                "measured_ratio": 1.0,
                "metrics": m,
            }
            for i, m in enumerate(metrics)
        ]

    areas: dict[str, Any] = {
        "GAZE": {
            "measured_ratio": 0.95,
            "take": {
                "audience_ratio": round(sum(aud) / n, 4),
                "script_ratio": round(sum(scr) / n, 4),
                "screen_ratio": 0.0,
                "away_ratio": round(max(0.0, 1 - sum(aud) / n - sum(scr) / n), 4),
                "reading_ratio": round(sum(scr) / n * 0.8, 4),
                "reading_runs": 1,
            },
            "slides": slide_rows(
                [
                    {
                        "audience_ratio": aud[i],
                        "script_ratio": scr[i],
                        "screen_ratio": 0.0,
                        "away_ratio": round(max(0.0, 1 - aud[i] - scr[i]), 4),
                        "reading_ratio": round(scr[i] * 0.8, 4),
                        "reading_runs": 0,
                    }
                    for i in range(n)
                ]
            ),
            "unmeasured_reason": None,
        },
        "SPEED": {
            "measured_ratio": 1.0,
            "take": {"cpm": round(sum(sp) / n, 1), "pause_count": 3},
            "slides": slide_rows([{"cpm": sp[i], "pause_count": 1} for i in range(n)]),
            "unmeasured_reason": None,
        },
        "VOLUME": {
            "measured_ratio": 1.0,
            "take": {
                "level_db": -29.5,
                "voice_diff_db": voice_diff_db,
                "low_ratio": 0.0,
                "base_level_source": "CALIBRATION",
            },
            "slides": slide_rows(
                [
                    {"level_db": -29.5, "voice_diff_db": voice_diff_db, "low_ratio": 0.0}
                    for _ in range(n)
                ]
            ),
            "unmeasured_reason": None,
        },
        "PAUSE": {
            "measured_ratio": 1.0,
            "take": {"long_silence_count": long_silence_count},
            "slides": slide_rows([{"long_silence_count": 0} for _ in range(n)]),
            "unmeasured_reason": None,
        },
        "FILLER": {
            "measured_ratio": 1.0,
            "take": {
                "filler_count": filler_count,
                "filler_per_min": filler_per_min,
                "by_tier": {},
                "by_word": {},
            },
            "slides": slide_rows(
                [
                    {
                        "filler_count": round(filler_count / n),
                        "filler_per_min": filler_per_min,
                        "by_tier": {},
                        "by_word": {},
                    }
                    for _ in range(n)
                ]
            ),
            "unmeasured_reason": None,
        },
        "TIME": {
            "measured_ratio": 1.0,
            "take": {"duration_ms": float(duration)},
            "slides": slide_rows([{"slide_duration_ms": float(slide_ms)} for _ in range(n)]),
            "unmeasured_reason": None,
        },
    }
    for area, reason in (unmeasured or {}).items():
        a = areas[area]
        a["take"] = None
        a["measured_ratio"] = 0.1
        a["unmeasured_reason"] = reason
        for s in a["slides"]:
            s["metrics"] = {k: None for k in s["metrics"]}
            s["measured_ratio"] = 0.1
    segs = []
    for issue, slide, reliable in issues or []:
        area = (
            "SPEED"
            if issue.startswith("PACE")
            else "GAZE"
            if issue.startswith("GAZE")
            else "FILLER"
            if issue.startswith("FILLER")
            else "PAUSE"
            if issue == "LONG_SILENCE"
            else "VOLUME"
            if issue == "VOLUME_LOW"
            else "TIME"
        )
        start = ((slide or 1) - 1) * slide_ms
        segs.append(
            {
                "area": area,
                "issue_type": issue,
                "slide_number": slide,
                "start_ms": start,
                "end_ms": start + slide_ms // 2,
                "mean_severity": 1.0,
                "reliable": reliable,
                "coached": False,
            }
        )
    return {
        "take_id": take_id,
        "duration_ms": duration,
        "script_mode": script_mode,
        "plan": plan
        or {"target_ms": duration, "min_ms": duration - 30_000, "max_ms": duration + 30_000},
        "replayed": False,
        "criteria_changed": False,
        "areas": areas,
        "problem_segments": segs,
        "interventions": [],
        "gave_up": [],
        "criteria": _criteria(criteria_versions),
    }


def review(
    *,
    take_id: str,
    dimensions: dict[str, str] | None = None,
    score: int | None = 72,
    fallback: bool = False,
) -> dict[str, Any]:
    """리뷰 응답 하나(저장된 payload 모양). 차원 상태를 주지 않으면 모두 STABLE."""
    dims = {
        "audience_gaze": "STABLE",
        "script_independence": "STABLE",
        "pacing_rhythm": "STABLE",
        "vocal_delivery": "STABLE",
        "fluency": "STABLE",
        "content_delivery": "NOT_EVALUABLE",
    }
    dims.update(dimensions or {})
    return {
        "take_id": take_id,
        "overall_summary": "가상 리뷰 요약입니다.",
        "score": score,
        "dimensions": dims,
        "strengths": [],
        "improvements": [],
        "comparison": [],
        "mission_results": [],
        "criteria_results": [],
        "next_missions": [],
        "recurring_issues": [],
        "fallback_reason": "LLM_UNAVAILABLE" if fallback else None,
        "meta": {
            "schema_version": "1.0",
            "feature_version": "review-1.2",
            "criteria_versions": {
                "review": "review-1.2+99c57adfbbb0",
                "gaze": CRITERIA_VERSIONS["GAZE"],
            },
            "model": None,
        },
    }


@dataclass
class TakeRecord:
    """BE의 Take 한 행과 저장된 AI 결과(가상)."""

    take_id: str
    take_number: int
    status: str = "COMPLETED"
    mode: str = "COACHING"
    script_mode: str = "OFF"
    end_reason: str | None = None
    page_count: int | None = 4
    visited_slides: list[int] = field(default_factory=lambda: [1, 2, 3, 4])
    script_version_id: str = "sv-1"
    presentation_version_id: str = "pv-1"
    standard_version: int | None = 1
    completed_at: str | None = "2026-11-01T10:00:00+09:00"
    started_at: str = "2026-11-01T09:50:00+09:00"
    take_result: dict[str, Any] | None = None
    script_coverage: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    revisions: dict[str, int | None] = field(
        default_factory=lambda: {"TAKE_RESULT": 1, "SCRIPT_COVERAGE": None, "REVIEW": 1}
    )


def completed_fully(t: TakeRecord) -> bool | None:
    """BE가 기존 기록으로 계산하는 '끝까지 한 발표'(기능 문서 12-3).

    완료 + 직접 끝냄(end_reason 없음) + page_count 양수 + 장 넘김 기록이 1..page_count 를 모두 포함 → True.
    중단(ABANDONED · CLOSED) → False. 자동 종료(SILENCE · TIME_LIMIT) · 장 기록 일부 누락 · page_count 없음 → None.
    """
    if t.end_reason in ("ABANDONED", "CLOSED") or t.status in ("ABANDONED", "CLOSED"):
        return False
    if t.status != "COMPLETED":
        return None
    if t.end_reason is not None:
        return None
    if not t.page_count or t.page_count <= 0:
        return None
    if set(range(1, t.page_count + 1)) <= set(t.visited_slides):
        return True
    return None


def take_summary(t: TakeRecord) -> dict[str, Any]:
    """기본 자료의 TakeSummary(5-2)."""
    rv = t.review
    return {
        "take_id": t.take_id,
        "take_number": t.take_number,
        "status": t.status,
        "mode": t.mode,
        "script_mode": t.script_mode,
        "end_reason": t.end_reason,
        "completed_fully": completed_fully(t),
        "script_version_id": t.script_version_id,
        "presentation_version_id": t.presentation_version_id,
        "standard_version": t.standard_version,
        "completed_at": t.completed_at,
        "review": (
            {
                "revision": t.revisions.get("REVIEW") or 0,
                "score": rv.get("score"),
                "dimensions": dict(rv.get("dimensions") or {}),
            }
            if rv
            else None
        ),
    }


def take_snapshot(t: TakeRecord, *, omitted_paths: list[str] | None = None) -> dict[str, Any]:
    """기본 자료의 TakeSnapshot(5-2). 결과 payload 는 그대로(깊은 복사) 싣는다."""
    return {
        "summary": take_summary(t),
        "results": {
            "take_result": copy.deepcopy(t.take_result),
            "script_coverage": copy.deepcopy(t.script_coverage),
            "review": copy.deepcopy(t.review),
        },
        "revisions": {
            "TAKE_RESULT": t.revisions.get("TAKE_RESULT") if t.take_result else None,
            "SCRIPT_COVERAGE": t.revisions.get("SCRIPT_COVERAGE") if t.script_coverage else None,
            "REVIEW": t.revisions.get("REVIEW") if t.review else None,
        },
        "omitted_paths": list(omitted_paths or []),
    }


@dataclass
class PitchRecord:
    """BE의 피치 한 행과 그 Take들(가상)."""

    pitch_id: str
    user_id: str
    title: str
    time_limit_sec: int = 240
    lower_deviation_sec: int = 30
    upper_deviation_sec: int = 30
    script_version_id: str = "sv-1"
    script_version_label: str = "V1"
    presentation_version_id: str = "pv-1"
    page_count: int | None = 4
    standard_version: int | None = 1
    criteria: dict[str, Any] | None = None
    missions: list[dict[str, Any]] = field(default_factory=list)
    takes: list[TakeRecord] = field(default_factory=list)
    last_practiced_at: str | None = "2026-11-01T10:00:00+09:00"

    def take(self, take_id: str) -> TakeRecord | None:
        return next((t for t in self.takes if t.take_id == take_id), None)


def pitch_summary(p: PitchRecord) -> dict[str, Any]:
    reviewed = [t for t in p.takes if t.review]
    last = max(reviewed, key=lambda t: t.take_number) if reviewed else None
    return {
        "pitch_id": p.pitch_id,
        "title": p.title,
        "take_count": len(p.takes),
        "last_practiced_at": p.last_practiced_at,
        "last_review": (
            {
                "take_id": last.take_id,
                "take_number": last.take_number,
                "score": last.review.get("score"),
                "priority_dimensions": [
                    d for d, s in last.review["dimensions"].items() if s == "PRIORITY"
                ],
            }
            if last
            else None
        ),
    }


def pitch_context(p: PitchRecord, *, focus_take_id: str | None) -> dict[str, Any]:
    """기본 자료의 PitchContext(5-3): 최근 Take 10개 · 보던 Take(없으면 가장 최근 완료 Take)."""
    recent = sorted(p.takes, key=lambda t: t.take_number, reverse=True)[:10]
    focus = p.take(focus_take_id) if focus_take_id else None
    if focus is None:
        done = [t for t in recent if t.status == "COMPLETED"]
        focus = done[0] if done else None
    lim = p.time_limit_sec * 1000
    return {
        "info": {
            "pitch_id": p.pitch_id,
            "title": p.title,
            "time_limit_ms": lim,
            "min_ms": lim - p.lower_deviation_sec * 1000,
            "max_ms": lim + p.upper_deviation_sec * 1000,
        },
        "versions": {
            "script_version_id": p.script_version_id,
            "script_version_label": p.script_version_label,
            "presentation_version_id": p.presentation_version_id,
            "page_count": p.page_count,
            "standard_version": p.standard_version,
        },
        "takes": [take_summary(t) for t in recent],
        "focus_take": take_snapshot(focus) if focus else None,
        "missions": copy.deepcopy(p.missions),
        "criteria": copy.deepcopy(p.criteria),
    }


def sample_pitch(user_id: str = "u-3") -> PitchRecord:
    """대표 시나리오(3-1 · 3-2)의 '졸업 발표': Take 4 · 5 모두 3장 시선 비율이 낮다(31% · 28%)."""
    p = PitchRecord(
        pitch_id="p-1",
        user_id=user_id,
        title="졸업 발표",
        criteria={
            "standard_version": 1,
            "display_criteria": ["청중을 보며 말하기"],
            "targets": [],
            "script_used": None,
        },
        missions=[
            {
                "mission_id": "m-7",
                "area": "GAZE",
                "slide_number": 3,
                "target": {"metric": "audience_ratio", "operator": "GTE", "value": 0.4},
                "status": "ACTIVE",
            }
        ],
    )
    for n, aud3 in ((3, 0.45), (4, 0.31), (5, 0.28)):
        tid = f"t-{n}"
        p.takes.append(
            TakeRecord(
                take_id=tid,
                take_number=n,
                completed_at=f"2026-11-0{n - 2}T10:00:00+09:00",
                take_result=take_result(
                    take_id=tid,
                    audience=[0.62, 0.58, aud3, 0.6],
                    script_ratio=[0.15, 0.2, 0.55, 0.18],
                    issues=[("GAZE_ON_SCRIPT", 3, True)] if aud3 < 0.4 else [],
                ),
                review=review(
                    take_id=tid,
                    dimensions={
                        "audience_gaze": "PRIORITY" if aud3 < 0.4 else "STABLE",
                        "script_independence": "PRIORITY" if aud3 < 0.4 else "STABLE",
                        "fluency": "STRENGTH",
                    },
                    score=68 if aud3 < 0.4 else 76,
                ),
            )
        )
    return p
