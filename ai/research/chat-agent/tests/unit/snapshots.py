"""시험용 Take 스냅샷 만들기 (코치 결과 예시 `coach/examples/finalize/response.json`의 모양을 따른다).

- 누가 쓰나: values · goals · claims 단위 시험(그리고 같은 모양의 스냅샷이 필요한 다른 시험).
- 쓰는 법
  ```python
  t4 = make_take(4, audience_ratio=0.28)                       # Take 4, 청중 시선 28%
  t5 = make_take(5, gaze_slides={3: 0.28}, issues=[("PACE_FAST", 2, True)],
                 dims={"audience_gaze": "PRIORITY"})            # 3장 시선 28%, 2장 빠름 문제, 리뷰 차원 지정
  takes = takes_map(t4, t5)                                    # {take_id: 스냅샷}
  reg, version_of = new_registry(), make_version_of("p-1")
  ```
- 기본값(일부러 고정): 대본 `sv-1` · 평가 기준 `standard_version=1` · `completed_fully=True` · `script_mode=HIGHLIGHT` ·
  청중 시선 0.31 · 말 속도 300(기준 275~350) · 분당 군더더기 1.3 · 발표 시간 92000ms(계획 270000~330000) ·
  장 1~3 · 리뷰 차원은 `DEFAULT_DIMS` · 문제 구간 없음 · 대본 전달도 없음(`scored_ratio=None`).
- 고칠 때: 값 하나만 비틀려면 `set_value(...)`로 만든 뒤 바꾼다(스냅샷은 바꿀 수 있는 dict를 가진다).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from chat_agent.evidence import EvidenceRegistry, RecordVersion
from chat_agent.schemas import TakeSnapshot
from chat_agent.values import ISSUE_AREA

DEFAULT_CRITERIA: dict[str, str] = {
    "GAZE": "gaze-1.3+aaaaaaaaaaaa",
    "SPEED": "pace-1.0+bbbbbbbbbbbb",
    "VOLUME": "volume-1.1+cccccccccccc",
    "PAUSE": "volume-1.1+cccccccccccc",
    "FILLER": "filler-1.0+dddddddddddd",
    "TIME": "timing-1.1+eeeeeeeeeeee",
}
#: 리뷰 차원 기본 상태 (청중 시선이 우선 개선: 코드 판정 예시 "시선 잘 봤어" → CONTRADICTED)
DEFAULT_DIMS: dict[str, str] = {
    "audience_gaze": "PRIORITY",
    "script_independence": "STABLE",
    "pacing_rhythm": "STRENGTH",
    "vocal_delivery": "STRENGTH",
    "fluency": "IMPROVED",
    "content_delivery": "NOT_EVALUABLE",
}
_UNSET: Any = object()


def make_take(
    take_number: int,
    *,
    take_id: str | None = None,
    audience_ratio: float | None = 0.31,
    gaze_slides: Mapping[int, float | None] | None = None,
    cpm: float | None = 300.0,
    cpm_slides: Mapping[int, float | None] | None = None,
    filler_per_min: float | None = 1.3,
    duration_ms: float | None = 92000.0,
    slide_numbers: Sequence[int] = (1, 2, 3),
    slide_targets: Mapping[int, float | None] | None = None,
    slide_durations: Mapping[int, float] | None = None,
    issues: Iterable[tuple[str, int | None, bool]] = (),
    dims: Mapping[str, str] | None = _UNSET,
    script_mode: str | None = "HIGHLIGHT",
    completed_fully: bool | None = True,
    completed: bool = True,
    script_version_id: str | None = "sv-1",
    presentation_version_id: str | None = "pv-1",
    standard_version: int | None = 1,
    criteria_versions: Mapping[str, str | None] | None = None,
    pace_thresholds: tuple[float | None, float | None] = (275.0, 350.0),
    plan: Mapping[str, float | None] | None = _UNSET,
    unmeasured: Mapping[str, str] | None = None,
    scored_ratio: float | None = None,
    content_coverage: float | None = 0.8,
    key_points: Sequence[tuple[int, str]] = (),
    omitted_paths: Sequence[str] = (),
    with_take_result: bool = True,
) -> TakeSnapshot:
    """Take 하나의 스냅샷.

    Args:
        take_number: Take 번호. `take_id`는 기본 `take-<번호>`.
        audience_ratio, gaze_slides: Take 청중 시선과 장별 값(없는 장은 Take 값을 따른다).
        cpm, cpm_slides, filler_per_min, duration_ms: 속도 · 군더더기 · 발표 시간. `None`이면 그 값이 null(미측정 값).
        slide_numbers: 영역마다 가진 장 번호들. `slide_targets`는 장 목표 시간(기본 60000), `slide_durations`는 장 실제 시간.
        issues: 문제 구간 `(문제 이름, 장 번호 또는 None, reliable)`. 영역은 `ISSUE_AREA`로 정한다.
        dims: 리뷰 차원 상태 {차원: 상태}. 생략하면 `DEFAULT_DIMS`, `None`이면 리뷰 없음, 일부만 주면 나머지는 기본값을 쓰지 않는다.
        script_mode, completed_fully, completed: 요약 값. `completed=False`면 `completed_at`이 null이다.
        script_version_id, presentation_version_id, standard_version: 버전. `None`을 줄 수 있다.
        criteria_versions: 영역별 `criteria_version`(None이면 그 영역은 버전 없음). 기본 `DEFAULT_CRITERIA`.
        pace_thresholds: (PACE_SLOW 기준, PACE_FAST 기준). 기본 (275, 350).
        plan: 시간 계획 dict. 생략하면 270000~330000(목표 300000), `None`이면 계획 없음.
        unmeasured: {영역: unmeasured_reason}. 그 영역은 `take`가 null이고 장이 없다.
        scored_ratio: None이 아니면 `script_coverage`를 만든다(`take_scores.scored_ratio`). `key_points`는 (장, 상태) 목록.
        omitted_paths: 크기 때문에 뺀 경로. with_take_result=False면 take_result 자체가 없다.
    """
    tid = take_id or f"take-{take_number}"
    crit_versions = dict(DEFAULT_CRITERIA)
    crit_versions.update(criteria_versions or {})
    gaze_slides = dict(gaze_slides or {})
    cpm_slides = dict(cpm_slides or {})
    slide_targets = dict(slide_targets or {})
    slide_durations = dict(slide_durations or {})
    unmeasured = dict(unmeasured or {})

    def slide_list(area: str, build: Any) -> list[dict[str, Any]]:
        return [
            {
                "slide_number": n,
                "start_ms": 0,
                "end_ms": 1000,
                "target_ms": slide_targets.get(n, 60000.0),
                "measured_ratio": 1.0,
                "metrics": build(n),
            }
            for n in slide_numbers
        ]

    def area(name: str, take: dict[str, Any], build: Any) -> dict[str, Any]:
        reason = unmeasured.get(name)
        return {
            "measured_ratio": 0.0 if reason else 1.0,
            "take": None if reason else take,
            "slides": [] if reason else slide_list(name, build),
            "unmeasured_reason": reason,
        }

    areas = {
        "GAZE": area(
            "GAZE",
            {
                "audience_ratio": audience_ratio,
                "script_ratio": 0.2,
                "reading_ratio": 0.1,
                "reading_runs": 1,
            },
            lambda n: {"audience_ratio": gaze_slides.get(n, audience_ratio)},
        ),
        "SPEED": area(
            "SPEED", {"cpm": cpm, "pause_count": 1}, lambda n: {"cpm": cpm_slides.get(n, cpm)}
        ),
        "VOLUME": area(
            "VOLUME",
            {"voice_diff_db": -1.0, "low_ratio": 0.05},
            lambda n: {"voice_diff_db": -1.0, "low_ratio": 0.05},
        ),
        "PAUSE": area("PAUSE", {"long_silence_count": 0}, lambda n: {"long_silence_count": 0}),
        "FILLER": area(
            "FILLER",
            {"filler_count": 2, "filler_per_min": filler_per_min},
            lambda n: {"filler_count": 0, "filler_per_min": filler_per_min},
        ),
        "TIME": area(
            "TIME",
            {"duration_ms": duration_ms},
            lambda n: {"slide_duration_ms": slide_durations.get(n, 30000.0)},
        ),
    }
    criteria: dict[str, Any] = {
        a: ({"criteria_version": v, "issues": {}} if v is not None else {"issues": {}})
        for a, v in crit_versions.items()
    }
    slow, fast = pace_thresholds
    pace_issues: dict[str, Any] = {}
    if slow is not None:
        pace_issues["PACE_SLOW"] = {"metric": "cpm", "threshold": slow}
    if fast is not None:
        pace_issues["PACE_FAST"] = {"metric": "cpm", "threshold": fast}
    criteria["SPEED"]["issues"] = pace_issues
    if plan is _UNSET:
        plan = {"target_ms": 300000, "min_ms": 270000, "max_ms": 330000}

    take_result: dict[str, Any] = {
        "take_id": tid,
        "duration_ms": duration_ms,
        "script_mode": script_mode,
        "plan": dict(plan) if plan is not None else None,
        "areas": areas,
        "problem_segments": [
            {
                "area": ISSUE_AREA[issue],
                "issue_type": issue,
                "slide_number": slide,
                "start_ms": 0,
                "end_ms": 5000,
                "mean_severity": 0.5,
                "reliable": reliable,
                "coached": False,
            }
            for issue, slide, reliable in issues
        ],
        "criteria": criteria,
    }

    script_coverage: dict[str, Any] | None = None
    if scored_ratio is not None:
        script_coverage = {
            "take_scores": {
                "scored_ratio": scored_ratio,
                "content_coverage": content_coverage,
                "critical_fact_accuracy": 0.9,
            },
            "evaluations": [
                {
                    "slide_number": s,
                    "scores": {"content_coverage": content_coverage},
                    "key_points": [{"importance": "high", "status": st}],
                }
                for s, st in key_points
            ],
            "failed": [],
        }

    review_dims = (
        dict(DEFAULT_DIMS) if dims is _UNSET else (dict(dims) if dims is not None else None)
    )
    review = {"dimensions": review_dims, "score": 70} if review_dims is not None else None
    summary: dict[str, Any] = {
        "take_id": tid,
        "take_number": take_number,
        "status": "COMPLETED",
        "mode": "FULL",
        "script_mode": script_mode,
        "end_reason": None,
        "completed_fully": completed_fully,
        "script_version_id": script_version_id,
        "presentation_version_id": presentation_version_id,
        "standard_version": standard_version,
        "completed_at": f"2026-11-0{take_number % 9 + 1}T10:00:00Z" if completed else None,
        "review": {"revision": 1, "score": 70, "dimensions": review_dims}
        if review_dims is not None
        else None,
    }
    return TakeSnapshot.model_validate(
        {
            "summary": summary,
            "results": {
                "take_result": take_result if with_take_result else None,
                "script_coverage": script_coverage,
                "review": review,
            },
            "revisions": {
                "TAKE_RESULT": 1 if with_take_result else None,
                "SCRIPT_COVERAGE": 1 if script_coverage else None,
                "REVIEW": 1 if review else None,
            },
            "omitted_paths": list(omitted_paths),
        }
    )


def set_value(
    snap: TakeSnapshot, area: str, metric: str, value: Any, slide_number: int | None = None
) -> TakeSnapshot:
    """스냅샷의 값 하나를 바꾼다(손상된 값 · null 만들기). `slide_number`가 None이면 Take 값."""
    data = snap.results.take_result["areas"][area]  # type: ignore[index]
    if slide_number is None:
        data["take"][metric] = value
    else:
        next(s for s in data["slides"] if s["slide_number"] == slide_number)["metrics"][metric] = (
            value
        )
    return snap


def takes_map(*snaps: TakeSnapshot) -> dict[str, TakeSnapshot]:
    """{take_id: 스냅샷} (코드 판정에 넘기는 모양)."""
    return {s.summary.take_id: s for s in snaps}


def new_registry(run_id: str = "run-1") -> EvidenceRegistry:
    return EvidenceRegistry(run_id=run_id)


def make_version_of(
    pitch_id: str = "p-1",
    *,
    script_version_id: str | None = "sv-1",
    presentation_version_id: str | None = "pv-1",
    standard_version: int | None = 1,
):  # noqa: ANN201
    """스냅샷 → 근거 지문용 `RecordVersion`.

    `script_version_id` · `presentation_version_id` · `standard_version`은 **피치의 판단 시점 버전**이다(5-2: "기록은
    피치마다 판단할 때의 대본 · 발표자료 · 평가 기준 버전"). Take 마다 다른 값은 결과 판(`results`)과 규칙 판
    (`rule_versions`)으로 드러난다. 이 인자를 바꾸면 같은 Take 값이라도 근거 지문이 바뀐다(6-3 압력).
    """

    def version_of(snap: TakeSnapshot) -> RecordVersion:
        s = snap.summary
        tr = snap.results.take_result or {}
        return RecordVersion(
            pitch_id=pitch_id,
            script_version_id=script_version_id,
            presentation_version_id=presentation_version_id,
            standard_version=standard_version,
            take_id=s.take_id,
            results={k: v for k, v in snap.revisions.model_dump().items() if v is not None},
            rule_versions={
                a: c.get("criteria_version") for a, c in (tr.get("criteria") or {}).items()
            },
        )

    return version_of
