"""Take 스냅샷에서 값 · 차원 상태 · 문제 구간 · 판정 범위를 읽는다 (기능 문서 6-3 · 6-4 '값 읽기 · 값 검사 · 범위').

- 누가 쓰나: goals.py(비교 · 목표 판정 · 목표 초안) · claims.py(측정 주장 판정) · tools/(도구가 기록 값을 읽을 때).
- 입력: `TakeSnapshot`(BE가 저장된 payload를 그대로 실어 준 것) · 지표 이름 · 장 번호 · `ChatAgentConfig`.
- 출력: `ReadValue`(상태 + 값 + 근거 경로 · 버전). 값이 없는 까닭을 상태로 나눠 돌려준다.
  `OK`만 값이 있고, 나머지는 값이 없는 것이다. 값 없음은 0 · 개선 · 악화 · 실패로 바꾸지 않는다(P9).
- 처리 순서
  1. 읽을 경로를 정한다(Take 값 `take_result.areas.<영역>.take.<지표>`, 장 값
     `take_result.areas.<영역>.slides[slide_number=N].metrics.<지표>`, 내용 전달 지표는
     `script_coverage.take_scores.<지표>`).
  2. 그 경로가 `omitted_paths` 아래면 `OMITTED`(크기 때문에 뺀 값을 '없는 값'으로 오인하지 않는다, L4-05).
  3. 결과가 아예 없으면 `NOT_LOADED`.
  4. 영역이 `unmeasured_reason`을 달았거나 값이 null이면 `UNMEASURED`. 내용 전달 지표는
     `scored_ratio >= cfg.min_scored_ratio`일 때만 값이 있다(리뷰 비교와 같은 조건).
  5. 값 검사: 유한 수(bool 제외) · 비율 0~1 · 시간/횟수 0 이상 · 장 번호 양수 + 영역 안 중복 없음.
     어기면 `INVALID_DATA`로 멈춘다(0으로 보정하지 않는다, L4-06).
- 고칠 때
  - 상태 이름을 바꾸면 goals.py · claims.py의 분기를 같이 고친다. 상태 이름은 L1-17(`NOT_FOUND` · `UNMEASURED` ·
    `OMITTED`)과 같게 둔다.
  - 영역 · 문제 이름 표(`METRIC_AREA` · `ISSUE_AREA`)는 코치 결과의 이름이다. 코치가 지표를 더하면 여기에도 더한다.
  - 서비스 패키지를 import 하지 않는다(코어는 pydantic · 표준 라이브러리만).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from .config import ChatAgentConfig
from .schemas import TakeSnapshot

ReadState = Literal[
    "OK",
    "NOT_LOADED",
    "OMITTED",
    "UNMEASURED",
    "NOT_FOUND",
    "EMPTY",
    "INVALID_DATA",
]

#: 지표 → 코치 결과의 영역. 내용 전달 지표는 영역이 아니라 대본 전달도 채점에서 읽는다
METRIC_AREA: dict[str, str] = {
    "audience_ratio": "GAZE",
    "script_ratio": "GAZE",
    "reading_ratio": "GAZE",
    "reading_runs": "GAZE",
    "screen_ratio": "GAZE",
    "away_ratio": "GAZE",
    "cpm": "SPEED",
    "pause_count": "SPEED",
    "long_silence_count": "PAUSE",
    "duration_ms": "TIME",
    "slide_duration_ms": "TIME",
    "voice_diff_db": "VOLUME",
    "low_ratio": "VOLUME",
    "filler_per_min": "FILLER",
    "filler_count": "FILLER",
}
#: 대본 전달도 채점에서 읽는 지표
CONTENT_METRICS: tuple[str, ...] = ("content_coverage", "critical_fact_accuracy")
#: 문제 이름 → 영역 (코치 `problem_segments[].area`)
ISSUE_AREA: dict[str, str] = {
    "GAZE_LOW_EYE_CONTACT": "GAZE",
    "GAZE_AWAY": "GAZE",
    "GAZE_ON_SCRIPT": "GAZE",
    "GAZE_ON_SCREEN": "GAZE",
    "PACE_FAST": "SPEED",
    "PACE_SLOW": "SPEED",
    "LONG_SILENCE": "PAUSE",
    "BEHIND_SCHEDULE": "TIME",
    "AHEAD_OF_SCHEDULE": "TIME",
    "SLIDE_OVER": "TIME",
    "TIME_OVER": "TIME",
    "FINAL_MINUTE": "TIME",
    "VOLUME_LOW": "VOLUME",
    "FILLER_FREQUENT": "FILLER",
}
#: 내용 전달의 '문제' 이름과 문제로 세는 핵심 내용 상태 (리뷰 vocab.CONTENT_ISSUE · CONTENT_BURDEN_STATUSES)
CONTENT_ISSUE = "CONTENT"
CONTENT_PROBLEM_STATUSES = frozenset({"missing", "contradicted"})


class InvalidDataError(ValueError):
    """값 · 범위가 계약을 어겨 계산을 멈춰야 할 때(`range_for`). 상태 `INVALID_DATA`에 해당한다(L4-06)."""


@dataclass(frozen=True)
class ReadValue:
    """읽은 값 하나와 그 출처 (근거 `Evidence`를 만드는 재료).

    - `state`: `OK`일 때만 `value`가 있다. 그 밖은 값이 없는 까닭이다.
    - `value`: 지표 값(숫자) 또는 차원 상태(문자열). 없으면 None.
    - `path`: 근거 경로. 예: `take_result.areas.GAZE.slides[slide_number=3].metrics.audience_ratio`(5-2).
    - `kind`: `TAKE_RESULT` · `SCRIPT_COVERAGE` · `REVIEW`. `revision`: 그 결과 판 번호.
    - `rule_version`: `take_result.criteria.<영역>.criteria_version`(없으면 None). 값이 없어도 읽을 수 있으면 채운다
      (목표 판정이 버전 차이를 값 없음보다 먼저 보기 때문, 6-4 판정 순서).
    - `reason`: 상태의 까닭 코드(예: `LOW_SCORED_RATIO` · `NO_REVIEW` · `SLIDE_MISSING`).
    """

    state: ReadState
    value: float | int | str | None
    path: str
    area: str | None
    kind: str
    revision: int | None
    rule_version: str | None
    slide_number: int | None
    metric: str | None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.state == "OK"


@dataclass(frozen=True)
class ProblemRead:
    """문제 구간 읽기 결과. `problem_issues`는 이것의 (state, issues) 짝이다.

    - `issues`: 믿을 수 있는(`reliable: true`) 구간의 문제 이름(중복 없이, 나온 순서).
    - `reason`: `UNRELIABLE_SEGMENTS`(믿을 수 없는 구간만 있음) · `AREA_UNMEASURED` · `SLIDE_UNMEASURED` 등.
    - `path`: 근거 경로. `revision`: 결과 판.
    """

    state: ReadState
    issues: list[str]
    reason: str | None
    path: str
    kind: str
    revision: int | None
    rule_version: str | None = None


# ── 공통 도우미 ───────────────────────────────────────────────────────────


def is_number(value: Any) -> bool:
    """유한한 수인가(bool 제외). NaN · 무한대 · 문자열은 False."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _path_related(omitted: str, path: str, include_descendants: bool) -> bool:
    """`omitted`가 `path`와 같거나 조상(또는 자손)인가. 경계는 `.` · `[` 로만 인정한다(`issues.PACE`는 `issues.PACE_FAST`의 조상이 아니다)."""
    if omitted == path or path.startswith((omitted + ".", omitted + "[")):
        return True
    return include_descendants and omitted.startswith((path + ".", path + "["))


def is_omitted(snapshot: TakeSnapshot, path: str, *, include_descendants: bool = True) -> bool:
    """읽으려는 `path`가 크기 때문에 뺀 경로(`omitted_paths`)에 걸리는가(6-12, L4-05).

    생략 경로 O에 대해 ① O == path ② O가 path의 조상 ③ O가 path의 자손이면 True다. 자손이 빠졌다면
    path 아래 값 일부를 읽을 수 없으니 남은 값(옛 경계값 등)으로 계산하지 않는다.
    `include_descendants=False`는 조상 · 같음만 본다(영역 전체처럼 일부 하위 값만 필요 없는 큰 경로의 존재 확인용).
    """
    return any(_path_related(o, path, include_descendants) for o in snapshot.omitted_paths)


def metric_value_ok(metric: str, value: Any, cfg: ChatAgentConfig) -> bool:
    """지표 값이 계약에 맞는가: 유한 수 · 비율 0~1 · 시간/횟수/분당 값 0 이상(6-4 값 검사)."""
    if not is_number(value):
        return False
    unit = cfg.metric_units.get(metric)
    if unit == "ratio":
        return 0 <= value <= 1
    if unit in ("count", "ms", "per_min", "cpm"):
        return value >= 0
    return True


def _slide_path(area_path: str, slide_number: int, metric: str) -> str:
    return f"{area_path}.slides[slide_number={slide_number}].metrics.{metric}"


def _validate_slides(slides: Any) -> bool:
    """장 목록이 계약에 맞는가: 객체 · 양수 정수 번호 · 중복 없음."""
    if not isinstance(slides, list):
        return False
    seen: set[int] = set()
    for s in slides:
        if not isinstance(s, dict):
            return False
        n = s.get("slide_number")
        if isinstance(n, bool) or not isinstance(n, int) or n < 1 or n in seen:
            return False
        seen.add(n)
    return True


def _measured_area(data: Any) -> bool:
    """리뷰의 `measured_areas`와 같은 기준: 영역이 있고 `unmeasured_reason`이 없다."""
    return isinstance(data, dict) and data.get("unmeasured_reason") is None


# ── 지표 값 ──────────────────────────────────────────────────────────────


def read_metric(
    snapshot: TakeSnapshot, metric: str, slide_number: int | None, cfg: ChatAgentConfig
) -> ReadValue:
    """Take 하나의 지표 값을 읽는다(Take 값 또는 장 값).

    Args:
        snapshot: BE가 실어 준 Take 스냅샷.
        metric: 지표 이름(예: `audience_ratio`). 모르는 이름이면 `NOT_FOUND`(`UNKNOWN_METRIC`).
        slide_number: 장 번호(양수). None이면 Take 값.
        cfg: 설정(`min_scored_ratio` · `metric_units`).

    Returns:
        `ReadValue`. 시간 단위는 ms, 비율은 0~1 그대로다(화면 반올림은 하지 않는다, 6-4).
        실패하지 않고 상태로 돌려준다: 경로가 `omitted_paths` 아래면 `OMITTED`, 결과가 없으면 `NOT_LOADED`,
        미측정 · null이면 `UNMEASURED`, 항목이 없으면 `NOT_FOUND`, 값 검사 위반이면 `INVALID_DATA`.

    예:
        >>> rv = read_metric(snap, "audience_ratio", 3, cfg)  # doctest: +SKIP
        >>> rv.path
        'take_result.areas.GAZE.slides[slide_number=3].metrics.audience_ratio'
    """
    content = metric in CONTENT_METRICS
    area = "CONTENT" if content else METRIC_AREA.get(metric)
    kind = "SCRIPT_COVERAGE" if content else "TAKE_RESULT"
    revision = snapshot.revisions.SCRIPT_COVERAGE if content else snapshot.revisions.TAKE_RESULT
    if area is None:
        return ReadValue(
            "NOT_FOUND", None, f"take_result.areas.?.take.{metric}", None, kind, revision, None,
            slide_number, metric, "UNKNOWN_METRIC",
        )  # fmt: skip

    if content:
        if slide_number is None:
            path = f"script_coverage.take_scores.{metric}"
        else:
            path = f"script_coverage.evaluations[slide_number={slide_number}].scores.{metric}"
    else:
        area_path = f"take_result.areas.{area}"
        path = (
            f"{area_path}.take.{metric}"
            if slide_number is None
            else _slide_path(area_path, slide_number, metric)
        )

    base = ReadValue("NOT_FOUND", None, path, area, kind, revision, None, slide_number, metric)

    def fail(state: ReadState, reason: str, **kw: Any) -> ReadValue:
        return replace(base, state=state, reason=reason, **kw)

    if slide_number is not None and (
        isinstance(slide_number, bool) or not isinstance(slide_number, int) or slide_number < 1
    ):
        return fail("INVALID_DATA", "BAD_SLIDE_NUMBER")
    # 판정 기준 버전은 값이 생략돼도 읽을 수 있으면 먼저 읽는다: 목표 판정은 버전 차이를 값 없음보다 먼저 본다(6-4).
    # 버전 경로(`take_result.criteria.<영역>`) 자체가 생략됐으면 읽지 못한 것이다
    tr = None if content else snapshot.results.take_result
    if tr is not None and not is_omitted(snapshot, f"take_result.criteria.{area}.criteria_version"):
        criteria = tr.get("criteria")
        crit = criteria.get(area) if isinstance(criteria, dict) else None
        if isinstance(crit, dict) and crit.get("criteria_version") is not None:
            version = crit["criteria_version"]
            if not isinstance(version, str) or not version:
                return fail("INVALID_DATA", "BAD_RULE_VERSION")
            base = replace(base, rule_version=version)
    if is_omitted(snapshot, path):
        return fail("OMITTED", "OMITTED_PATH")
    if content:
        return _read_content(snapshot, metric, slide_number, cfg, base, fail)

    if tr is None:
        return fail("NOT_LOADED", "NO_TAKE_RESULT")
    areas = tr.get("areas")
    if not isinstance(areas, dict):
        return fail("INVALID_DATA", "BAD_AREAS")

    def fail2(state: ReadState, reason: str) -> ReadValue:
        return replace(base, state=state, reason=reason)

    if area not in areas:
        return fail2("NOT_FOUND", "AREA_MISSING")
    data = areas[area]
    if data is None:
        return fail2("UNMEASURED", "AREA_NULL")
    if not isinstance(data, dict):
        return fail2("INVALID_DATA", "BAD_AREA")
    if data.get("unmeasured_reason") is not None:
        return fail2("UNMEASURED", str(data["unmeasured_reason"]))
    if not _validate_slides(data.get("slides") or []):
        return fail2("INVALID_DATA", "BAD_SLIDES")

    if slide_number is None:
        take = data.get("take")
        if take is None:
            return fail2("UNMEASURED", "TAKE_NULL")
        if not isinstance(take, dict):
            return fail2("INVALID_DATA", "BAD_TAKE_VALUES")
        container: dict[str, Any] = take
    else:
        slide = next(
            (s for s in data.get("slides") or [] if s["slide_number"] == slide_number), None
        )
        if slide is None:
            return fail2("NOT_FOUND", "SLIDE_MISSING")
        ratio = slide.get("measured_ratio")
        if is_number(ratio) and ratio <= 0:
            return fail2("UNMEASURED", "SLIDE_UNMEASURED")
        metrics = slide.get("metrics")
        if not isinstance(metrics, dict):
            return fail2("INVALID_DATA", "BAD_SLIDE_METRICS")
        container = metrics

    if metric not in container:
        return fail2("NOT_FOUND", "METRIC_MISSING")
    value = container[metric]
    if value is None:
        return fail2("UNMEASURED", "NULL_VALUE")
    if not metric_value_ok(metric, value, cfg):
        return fail2("INVALID_DATA", "BAD_VALUE")
    return replace(base, state="OK", value=value)


def _read_content(
    snapshot: TakeSnapshot,
    metric: str,
    slide_number: int | None,
    cfg: ChatAgentConfig,
    base: ReadValue,
    fail: Any,
) -> ReadValue:
    """내용 전달 지표: `scored_ratio`가 기준 이상일 때만 값이 있다(리뷰 비교와 같은 조건)."""
    sc = snapshot.results.script_coverage
    if sc is None:
        return fail("NOT_LOADED", "NO_SCRIPT_COVERAGE")
    # 채점 비율이 값의 조건이므로 그 경로가 생략됐으면 값도 판정할 수 없다(L4-05)
    if is_omitted(snapshot, "script_coverage.take_scores", include_descendants=False) or is_omitted(
        snapshot, "script_coverage.take_scores.scored_ratio"
    ):
        return fail("OMITTED", "OMITTED_PATH")
    scores = sc.get("take_scores")
    if not isinstance(scores, dict):
        return fail("UNMEASURED", "NO_TAKE_SCORES")
    ratio = scores.get("scored_ratio")
    if ratio is None:
        return fail("UNMEASURED", "NO_SCORED_RATIO")
    if not is_number(ratio) or not 0 <= ratio <= 1:
        return fail("INVALID_DATA", "BAD_SCORED_RATIO")
    if ratio < cfg.min_scored_ratio:
        return fail("UNMEASURED", "LOW_SCORED_RATIO")
    if slide_number is None:
        value = scores.get(metric)
    else:
        failed = {
            f.get("slide_number") if isinstance(f, dict) else f for f in sc.get("failed") or []
        }
        if slide_number in failed:
            return fail("UNMEASURED", "SLIDE_SCORING_FAILED")
        ev = next(
            (
                e
                for e in sc.get("evaluations") or []
                if isinstance(e, dict) and e.get("slide_number") == slide_number
            ),
            None,
        )
        if ev is None:
            return fail("NOT_FOUND", "SLIDE_MISSING")
        value = (ev.get("scores") or {}).get(metric)
    if value is None:
        return fail("UNMEASURED", "NULL_VALUE")
    if not metric_value_ok(metric, value, cfg):
        return fail("INVALID_DATA", "BAD_VALUE")
    return replace(base, state="OK", value=value)


# ── 리뷰 차원 상태 ────────────────────────────────────────────────────────

DIMENSION_STATES = ("IMPROVED", "PRIORITY", "STABLE", "STRENGTH", "NOT_EVALUABLE")


def read_dimension_state(snapshot: TakeSnapshot, dimension: str) -> ReadValue:
    """리뷰의 차원 상태를 읽는다(`results.review.dimensions.<차원>`, kind `REVIEW`).

    Returns:
        - 리뷰가 없으면 `EMPTY`(reason `NO_REVIEW`). 크기로 뺀 경우는 `OMITTED`.
        - 차원이 없으면 `NOT_FOUND`.
        - `NOT_EVALUABLE`은 판단 불가라 `UNMEASURED`(reason `NOT_EVALUABLE`, value도 `NOT_EVALUABLE`).
        - 모르는 상태 문자열은 `UNMEASURED`(reason `UNKNOWN_VALUE`)로 읽는다(BE의 새 상태에 죽지 않는다).
        - 그 밖은 `OK`와 상태 문자열(`STRENGTH` · `PRIORITY` · `IMPROVED` · `STABLE`).
    """
    path = f"review.dimensions.{dimension}"
    rev = snapshot.revisions.REVIEW

    def rv(state: ReadState, reason: str | None, value: str | None = None) -> ReadValue:
        return ReadValue(state, value, path, None, "REVIEW", rev, None, None, None, reason)

    if is_omitted(snapshot, path):
        return rv("OMITTED", "OMITTED_PATH")
    review = snapshot.results.review
    if review is None:
        return rv("EMPTY", "NO_REVIEW")
    dims = review.get("dimensions")
    if not isinstance(dims, dict):
        return rv("INVALID_DATA", "BAD_DIMENSIONS")
    if dimension not in dims:
        return rv("NOT_FOUND", "DIMENSION_MISSING")
    state = dims[dimension]
    if state == "NOT_EVALUABLE":
        return rv("UNMEASURED", "NOT_EVALUABLE", "NOT_EVALUABLE")
    if state not in DIMENSION_STATES:
        return rv("UNMEASURED", "UNKNOWN_VALUE")
    return rv("OK", None, state)


# ── 문제 구간 ────────────────────────────────────────────────────────────


def read_problems(
    snapshot: TakeSnapshot,
    dimension: str,
    slide_number: int | None,
    cfg: ChatAgentConfig,
    *,
    areas: tuple[str, ...] | None = None,
    issue_types: tuple[str, ...] | None = None,
) -> ProblemRead:
    """차원의 문제 구간을 읽는다(6-3 판정 표 머리). `problem_issues`의 자세한 형태.

    - `reliable: true`인 구간만 센다(리뷰가 부담에 세는 것과 같다).
    - 문제를 찾았으면 `OK` + 문제 이름. 못 찾았을 때 `OK`(빈 목록)는 **대상 영역이 모두 측정됐을 때만**이다.
      미측정 · 생략이면 빈 목록을 '문제 없음'으로 읽지 않고 `UNMEASURED`(·`OMITTED`)로 돌려준다.
    - 범위 안에 `reliable: false` 구간만 있으면 자료 부족이라 `UNMEASURED`(`UNRELIABLE_SEGMENTS`).
    - 장 범위(`slide_number` 있음)는 그 장이 영역의 장 목록에 있고 측정 비율이 0이 아니어야 '측정됨'이다.
    - 내용 전달(`content_delivery`)은 문제 구간이 아니라 대본 전달도의 `missing` · `contradicted` 핵심 내용을 센다
      (문제 이름 `CONTENT`).

    Args:
        areas: 읽을 영역을 줄인다(문제 주장은 그 문제의 영역만 보면 된다). None이면 차원의 모든 영역.
        issue_types: 셀 문제 이름을 줄인다. None이면 차원의 모든 문제.
    """
    spec = cfg.dimensions.get(dimension)
    if spec is None:
        return ProblemRead(
            "NOT_FOUND",
            [],
            "UNKNOWN_DIMENSION",
            "take_result.problem_segments",
            "TAKE_RESULT",
            None,
        )
    if dimension == "content_delivery":
        return _content_problems(snapshot, slide_number, cfg)

    kind = "TAKE_RESULT"
    rev = snapshot.revisions.TAKE_RESULT
    path = "take_result.problem_segments"

    def out(
        state: ReadState, issues: list[str], reason: str | None, rule: str | None = None
    ) -> ProblemRead:
        return ProblemRead(state, issues, reason, path, kind, rev, rule)

    want_areas = tuple(areas) if areas else spec.areas
    want_types = set(issue_types) & set(spec.issues) if issue_types else set(spec.issues)

    if is_omitted(snapshot, path) or any(
        is_omitted(snapshot, f"take_result.areas.{a}", include_descendants=False)
        for a in want_areas
    ):
        return out("OMITTED", [], "OMITTED_PATH")
    tr = snapshot.results.take_result
    if tr is None:
        return out("NOT_LOADED", [], "NO_TAKE_RESULT")
    area_map = tr.get("areas")
    if not isinstance(area_map, dict):
        return out("INVALID_DATA", [], "BAD_AREAS")

    measured = 0
    slide_unmeasured = False
    for a in want_areas:
        data = area_map.get(a)
        if not _measured_area(data):
            continue
        if not _validate_slides(data.get("slides") or []):
            return out("INVALID_DATA", [], "BAD_SLIDES")
        if slide_number is not None:
            slide = next(
                (s for s in data.get("slides") or [] if s["slide_number"] == slide_number), None
            )
            ratio = None if slide is None else slide.get("measured_ratio")
            if slide is None or (is_number(ratio) and ratio <= 0):
                slide_unmeasured = True
                continue
        measured += 1

    # 목록 키가 없거나 null 이면 자료 부족이다. 명시적인 빈 목록 `[]`만 '문제 없음'이 될 수 있다(6-3)
    segments = tr.get("problem_segments")
    if segments is None:
        return out("UNMEASURED", [], "SEGMENTS_MISSING")
    if not isinstance(segments, list):
        return out("INVALID_DATA", [], "BAD_SEGMENTS")
    issues: list[str] = []
    unreliable = 0
    for seg in segments:
        if not isinstance(seg, dict) or not isinstance(seg.get("issue_type"), str):
            return out("INVALID_DATA", [], "BAD_SEGMENT")
        if seg["issue_type"] not in want_types:
            continue
        seg_area = seg.get("area") or ISSUE_AREA.get(seg["issue_type"])
        if seg_area not in want_areas:
            continue
        seg_slide = seg.get("slide_number")
        if slide_number is not None and seg_slide != slide_number:
            continue
        reliable = seg.get("reliable")
        if not isinstance(reliable, bool):
            return out("INVALID_DATA", [], "BAD_SEGMENT")
        if reliable:
            if seg["issue_type"] not in issues:
                issues.append(seg["issue_type"])
        else:
            unreliable += 1
    if issues:
        return out("OK", issues, None)
    if unreliable:
        return out("UNMEASURED", [], "UNRELIABLE_SEGMENTS")
    if measured == len(want_areas):
        return out("OK", [], None)
    return out("UNMEASURED", [], "SLIDE_UNMEASURED" if slide_unmeasured else "AREA_UNMEASURED")


def _content_problems(
    snapshot: TakeSnapshot, slide_number: int | None, cfg: ChatAgentConfig
) -> ProblemRead:
    """내용 전달: 빠졌거나(`missing`) 틀린(`contradicted`) 핵심 내용을 문제로 센다."""
    path = "script_coverage.evaluations"
    rev = snapshot.revisions.SCRIPT_COVERAGE

    def out(state: ReadState, issues: list[str], reason: str | None) -> ProblemRead:
        return ProblemRead(state, issues, reason, path, "SCRIPT_COVERAGE", rev)

    if (
        is_omitted(snapshot, "script_coverage", include_descendants=False)
        or is_omitted(snapshot, path)
        or is_omitted(snapshot, "script_coverage.take_scores.scored_ratio")
        or is_omitted(snapshot, "script_coverage.failed")
    ):
        return out("OMITTED", [], "OMITTED_PATH")
    sc = snapshot.results.script_coverage
    if sc is None:
        return out("NOT_LOADED", [], "NO_SCRIPT_COVERAGE")
    scores = sc.get("take_scores")
    ratio = scores.get("scored_ratio") if isinstance(scores, dict) else None
    if ratio is None:
        return out("UNMEASURED", [], "NO_SCORED_RATIO")
    if not is_number(ratio) or not 0 <= ratio <= 1:
        return out("INVALID_DATA", [], "BAD_SCORED_RATIO")
    if ratio < cfg.min_scored_ratio:
        return out("UNMEASURED", [], "LOW_SCORED_RATIO")
    failed = {f.get("slide_number") if isinstance(f, dict) else f for f in sc.get("failed") or []}
    evals = [e for e in sc.get("evaluations") or [] if isinstance(e, dict)]
    if slide_number is not None:
        if slide_number in failed:
            return out("UNMEASURED", [], "SLIDE_SCORING_FAILED")
        evals = [e for e in evals if e.get("slide_number") == slide_number]
        if not evals:
            return out("UNMEASURED", [], "SLIDE_NOT_SCORED")
    for e in evals:
        for kp in e.get("key_points") or []:
            if isinstance(kp, dict) and kp.get("status") in CONTENT_PROBLEM_STATUSES:
                return out("OK", [CONTENT_ISSUE], None)
    if slide_number is None and failed:
        return out(
            "UNMEASURED", [], "SLIDES_FAILED"
        )  # 채점 못 한 장이 있어 '문제 없음'을 말할 수 없다
    return out("OK", [], None)


def problem_issues(
    snapshot: TakeSnapshot,
    dimension: str,
    slide_number: int | None,
    cfg: ChatAgentConfig,
    *,
    areas: tuple[str, ...] | None = None,
    issue_types: tuple[str, ...] | None = None,
) -> tuple[ReadState, list[str]]:
    """`read_problems`의 (상태, 문제 이름 목록) 짝. 규칙은 `read_problems`와 같다.

    예: 3장 시선에 믿을 수 있는 `GAZE_LOW_EYE_CONTACT` 구간이 있으면 ``("OK", ["GAZE_LOW_EYE_CONTACT"])``,
    측정됐고 없으면 ``("OK", [])``, 미측정이면 ``("UNMEASURED", [])``.
    """
    pr = read_problems(snapshot, dimension, slide_number, cfg, areas=areas, issue_types=issue_types)
    return pr.state, pr.issues


# ── 판정 범위 ────────────────────────────────────────────────────────────


class OmittedDataError(ValueError):
    """범위를 정하는 데 쓰는 경로가 크기 때문에 빠졌을 때(`range_for`). 상태 `OMITTED`에 해당한다(L4-05)."""


@dataclass(frozen=True)
class RangeSource:
    """범위 경계 하나의 출처 — 계산 근거(`DISTANCE`)가 입력으로 삼을 기록 값이다(L4-25).

    - `role`: `low` · `high` · `both`(아래 = 위, 목표 시간 하나에서 온 경우)
    - `path`: 근거 경로. 예: `take_result.criteria.SPEED.issues.PACE_FAST.threshold`
    """

    role: str
    path: str
    value: float


def _bound(value: Any, what: str) -> float | None:
    if value is None:
        return None
    if not is_number(value) or value < 0:
        raise InvalidDataError(f"{what} 값이 올바르지 않다: {value!r}")
    return float(value)


def _obj(value: Any, what: str) -> dict[str, Any]:
    """없으면 빈 객체, 객체가 아니면 손상(`INVALID_DATA`)."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InvalidDataError(f"{what}가 객체가 아니다")
    return value


def _check_omitted(snapshot: TakeSnapshot, path: str) -> None:
    if is_omitted(snapshot, path):
        raise OmittedDataError(path)


def range_sources(
    snapshot: TakeSnapshot, metric: str, slide_number: int | None, cfg: ChatAgentConfig
) -> tuple[tuple[float | None, float | None] | None, list[RangeSource]]:
    """`range_for`와 같되, 경계를 만든 기록 경로도 함께 돌려준다(계산 근거의 입력용).

    Returns:
        ((low, high) 또는 None, 쓴 경계의 출처 목록). 범위를 정할 수 없으면 (None, []).

    Raises:
        InvalidDataError: 경계가 유한한 비음수가 아니거나 low > high이거나 중첩 자료의 모양이 틀림.
        OmittedDataError: 쓸 경계의 경로가 `omitted_paths` 아래(생략돼 읽을 수 없음).
    """
    if cfg.direction.get(metric) != "RANGE":
        return None, []
    tr = snapshot.results.take_result
    if tr is None:
        return None, []
    low: float | None
    high: float | None
    sources: list[RangeSource] = []
    if metric == "cpm":
        pace_path = "take_result.criteria.SPEED.issues"
        # 쓰는 경계 경로만 본다(조상 · 같음 · 자손). 이름만 비슷한 다른 경로(`issues.PACE`)는 걸리지 않는다
        _check_omitted(snapshot, f"{pace_path}.PACE_SLOW.threshold")
        _check_omitted(snapshot, f"{pace_path}.PACE_FAST.threshold")
        issues = _obj(
            _obj(_obj(tr.get("criteria"), "criteria").get("SPEED"), "SPEED 기준").get("issues"),
            "issues",
        )
        slow = _obj(issues.get("PACE_SLOW"), "PACE_SLOW")
        fast = _obj(issues.get("PACE_FAST"), "PACE_FAST")
        low = _bound(slow.get("threshold"), "PACE_SLOW 기준")
        high = _bound(fast.get("threshold"), "PACE_FAST 기준")
        if low is not None:
            sources.append(RangeSource("low", f"{pace_path}.PACE_SLOW.threshold", low))
        if high is not None:
            sources.append(RangeSource("high", f"{pace_path}.PACE_FAST.threshold", high))
    elif metric == "duration_ms":
        for key in ("min_ms", "max_ms", "target_ms"):
            _check_omitted(
                snapshot, f"take_result.plan.{key}"
            )  # 쓰는 경계 경로만(조상 · 같음 · 자손)
        if is_omitted(snapshot, "take_result.plan", include_descendants=False):
            raise OmittedDataError("take_result.plan")
        plan = _obj(tr.get("plan"), "plan")
        low = _bound(plan.get("min_ms"), "plan.min_ms")
        high = _bound(plan.get("max_ms"), "plan.max_ms")
        if low is not None:
            sources.append(RangeSource("low", "take_result.plan.min_ms", low))
        if high is not None:
            sources.append(RangeSource("high", "take_result.plan.max_ms", high))
        if low is None and high is None:
            low = high = _bound(plan.get("target_ms"), "plan.target_ms")
            if low is not None:
                sources.append(RangeSource("both", "take_result.plan.target_ms", low))
    elif metric == "slide_duration_ms":
        if slide_number is None:
            return None, []
        slide_path = f"take_result.areas.TIME.slides[slide_number={slide_number}].target_ms"
        _check_omitted(snapshot, slide_path)
        time_area = _obj(_obj(tr.get("areas"), "areas").get("TIME"), "TIME 영역")
        slides = time_area.get("slides") or []
        if not isinstance(slides, list):
            raise InvalidDataError("TIME 장 목록이 목록이 아니다")
        target = next(
            (
                s.get("target_ms")
                for s in slides
                if isinstance(s, dict) and s.get("slide_number") == slide_number
            ),
            None,
        )
        low = high = _bound(target, "장 목표 시간")
        if low is not None:
            sources.append(RangeSource("both", slide_path, low))
    else:
        return None, []
    if low is None and high is None:
        return None, []
    if low is not None and high is not None and low > high:
        raise InvalidDataError(f"범위의 low({low})가 high({high})보다 크다")
    return (low, high), sources


def range_for(
    snapshot: TakeSnapshot, metric: str, slide_number: int | None, cfg: ChatAgentConfig
) -> tuple[float | None, float | None] | None:
    """범위가 좋은 지표의 그 Take 기준 (아래, 위)를 돌려준다(6-4 범위). 리뷰 비교와 같은 출처다.

    - `cpm`: 그 Take의 말 속도 판정 기준 — 아래 `criteria.SPEED.issues.PACE_SLOW.threshold`, 위 `PACE_FAST.threshold`.
    - `duration_ms`: 그 Take 계획의 `plan.min_ms` · `plan.max_ms`, 둘 다 없으면 `plan.target_ms`(아래 = 위).
    - `slide_duration_ms`: 그 장의 목표 시간 `areas.TIME.slides[].target_ms`(아래 = 위). 장 번호가 필요하다.

    Returns:
        (low, high). 한쪽이 없으면 그쪽은 열려 있어 None(JSON에는 null). 범위를 정할 수 없으면 None
        (방향이 RANGE가 아닌 지표 · 결과 없음 · 기준 없음). 호출한 쪽은 이를 `NO_RANGE`로 읽는다.

    Raises:
        InvalidDataError: 경계가 유한한 비음수가 아니거나 low > high이거나, 기준 자료(`criteria` · `plan` · 장 목록)의
            모양이 틀릴 때. 호출한 쪽은 `INVALID_DATA`로 계산을 멈춘다(0으로 보정하지 않는다, 6-4 값 검사).
        OmittedDataError: 경계를 읽을 경로가 크기 때문에 빠졌을 때. 호출한 쪽은 `OMITTED`로 보류한다.
    """
    return range_sources(snapshot, metric, slide_number, cfg)[0]


def volume_base_source(snap: TakeSnapshot | None) -> str | None:
    """그 Take 의 기준 음량 출처(`take_result.areas.VOLUME.take.base_level_source`, 예 `CALIBRATION` · `TAKE`). 없으면 None.

    소리 크기 비교(goals.py)와 표기(claims.py `volume_base`)가 같은 곳을 읽게 한다."""
    tr = snap.results.take_result if snap is not None else None
    area = (tr.get("areas") or {}).get("VOLUME") if isinstance(tr, Mapping) else None
    take = area.get("take") if isinstance(area, Mapping) else None
    src = take.get("base_level_source") if isinstance(take, Mapping) else None
    return src if isinstance(src, str) else None
