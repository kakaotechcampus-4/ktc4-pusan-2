"""두 Take 비교 · 목표 초안 만들기 · 목표 판정 (기능 문서 6-4 '비교 · 목표 계산', 6-7, L4-08~L4-12 · L4-25).

- 누가 쓰나: claims.py(`CHANGE` 주장 판정은 `compare_takes`) · tools/(비교 도구 · 목표 초안 · 목표 판정 도구) ·
  loop.py(자동 목표 확인, `TAKE_REVIEWED`).
- 입력: `TakeSnapshot` · 지표 · 장 번호 · `ChatAgentConfig` · 근거 등록부(`EvidenceRegistry`) · Take별 버전 정보를 만드는 함수
  (`version_of`: 근거 지문에 넣을 `RecordVersion`을 돌려준다. 이 모듈은 피치 id 외의 버전을 만들지 않는다).
- 출력: `Comparison`(5-2 `Comparison` 모양과 같다) · `GoalJudgement` · `GoalDraft` 또는 오류 코드 문자열.
  비교 · 판정에 쓴 값은 `registry`에 `RECORD` 근거로, 차이 · 거리 · 결과는 `COMPUTED` 근거로 발급한다
  (검사기가 입력 근거로 다시 계산할 수 있게 `calc` · `inputs`를 남긴다, L4-25).
- 처리 순서(비교)
  1. 방향 표에서 지표의 방향을 찾는다(없으면 `NO_DIRECTION`).
  2. 두 Take가 비교 자격이 있는지 본다: 둘 다 `completed_fully`, 대본 · 평가 기준 버전 같음, 값 있음,
     그 지표의 판정 기준 버전 같음, 범위 지표는 범위를 얻음. 하나라도 어기면 `comparable:false`와 까닭 코드.
  3. 원래 값이 같으면 `SAME`. 범위 지표는 각 Take의 범위 대비 거리 `d(x)=max(L-x, x-U, 0)`로, 그 밖은 방향표로 가른다.
  4. 값 · 차이 · 거리 · 결과의 근거를 발급한다.
- 처리 순서(목표 판정): 버전 차이(`BASELINE_MISMATCH`, 끝) → 필수 버전 누락(`INVALID_DATA`, 계속 기다림) →
  비교 불가(`UNKNOWN`, 다음 Take 기다림) → 이룸 · 못 이룸(끝). 목표에 **저장 때 고정한 값**만 쓰고 지금 설정의
  `min_delta`를 쓰지 않는다(L4-12).
- 고칠 때
  - 화면 표시용 반올림 값은 판정에 쓰지 않는다. 원래 값으로만 비교한다(6-4).
  - `min_delta` 경계는 부동소수 오차(예: 0.30 - 0.25 = 0.0499999…)만 `1e-9`로 받아 준다.
  - 근거: 범위 경계와 목표에 고정한 조건(기준 값 · 최소 개선폭 · 기준 범위)도 `RECORD` 근거로 발급하고 `COMPUTED`의
    `inputs`에 넣는다. 그래야 검사기가 근거만으로 거리 · 달성 여부를 다시 계산할 수 있다(L4-25).
  - 까닭 코드를 더하면 claims.py의 `UNVERIFIABLE` 이유와 7-9 문구 표를 같이 본다.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import ChatAgentConfig
from .evidence import EvidenceRegistry, RecordVersion
from .schemas import Goal, GoalDraft, Range, TakeSnapshot
from .values import (
    InvalidDataError,
    OmittedDataError,
    RangeSource,
    ReadValue,
    range_for,
    range_sources,
    read_metric,
)
from .version import SUPPORTED_GOAL_ALGORITHMS

Outcome = Literal["SAME", "BOTH_IN_RANGE", "IMPROVED", "WORSENED", "SAME_DISTANCE"]

#: 최소 개선폭 경계의 부동소수 오차 허용 (판정은 원래 값으로, 화면 반올림 값은 쓰지 않는다)
_EPS = 1e-9

VersionOf = Callable[[TakeSnapshot], RecordVersion]


class Comparison(BaseModel):
    """두 Take의 한 지표 비교 결과 (6-4 `Comparison`).

    - `comparable:false`면 `reason`(까닭 코드)만 의미가 있고 값 필드는 null이다.
    - 범위 필드(`previous_range` · `current_range` · `range_changed` · 두 거리)는 `RANGE` 지표에서만 채운다.
      무한대는 JSON에 쓰지 않고 `Range`의 null(열린 쪽)로 쓴다.
    """

    model_config = ConfigDict(extra="forbid")

    comparable: bool
    reason: str | None = None
    metric: str
    slide_number: int | None = None
    previous: float | None = None
    current: float | None = None
    outcome: Outcome | None = None
    previous_range: Range | None = None
    current_range: Range | None = None
    range_changed: bool | None = None
    previous_distance: float | None = None
    current_distance: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)


def range_distance(value: float, low: float | None, high: float | None) -> float:
    """범위 대비 거리 `d(x) = max(L - x, x - U, 0)`. 열린 쪽(None)은 무한대로 본다.

    예: 범위 275~350에서 값 300 → 0, 값 400 → 50, 값 250 → 25.
    """
    lo = -math.inf if low is None else low
    hi = math.inf if high is None else high
    return max(lo - value, value - hi, 0.0)


def record_read_value(
    registry: EvidenceRegistry,
    pitch_id: str,
    snap: TakeSnapshot,
    rv: ReadValue,
    cfg: ChatAgentConfig,
    version_of: VersionOf,
) -> str:
    """읽은 값(`ReadValue`)을 `RECORD` 근거로 발급하고 근거 id를 돌려준다. claims.py도 같이 쓴다.

    값이 OK인 읽기만 넘긴다(없는 값을 근거로 만들지 않는다). 규칙 버전 · 결과 판은 `rv`와 `version_of(snap)`에서 온다.
    """
    ev = registry.record(
        pitch_id=pitch_id,
        take_id=snap.summary.take_id,
        take_number=snap.summary.take_number,
        kind=rv.kind,
        revision=rv.revision,
        path=rv.path,
        value=rv.value if rv.value is not None else "",
        metric=rv.metric,
        slide_number=rv.slide_number,
        unit=cfg.metric_units.get(rv.metric or ""),
        rule_version=rv.rule_version,
        version=version_of(snap),
    )
    return ev.evidence_id


def _record_bound(
    registry: EvidenceRegistry,
    pitch_id: str,
    snap: TakeSnapshot,
    src: RangeSource,
    metric: str,
    slide_number: int | None,
    cfg: ChatAgentConfig,
    version_of: VersionOf,
) -> str:
    """범위 경계 하나를 `RECORD` 근거로 발급한다(L4-25)."""
    ev = registry.record(
        pitch_id=pitch_id,
        take_id=snap.summary.take_id,
        take_number=snap.summary.take_number,
        kind="TAKE_RESULT",
        revision=snap.revisions.TAKE_RESULT,
        path=src.path,
        value=src.value,
        metric=metric,
        slide_number=slide_number,
        unit=cfg.metric_units.get(metric),
        version=version_of(snap),
    )
    return ev.evidence_id


def _read_reason(*reads: ReadValue) -> str | None:
    """값을 못 읽은 까닭을 비교 불가 코드로 바꾼다. 모두 읽었으면 None."""
    states = {r.state for r in reads}
    if "INVALID_DATA" in states:
        return "INVALID_DATA"
    if "OMITTED" in states:
        return "OMITTED"
    if "NOT_LOADED" in states:
        return "NOT_LOADED"
    if states != {"OK"}:
        return "NO_VALUE"  # 미측정 · null · 항목 없음 (개선 · 악화로 바꾸지 않는다)
    return None


def _rng(r: tuple[float | None, float | None]) -> Range:
    return Range(low=r[0], high=r[1])


def compare_takes(
    prev: TakeSnapshot,
    cur: TakeSnapshot,
    metric: str,
    slide_number: int | None,
    *,
    pitch_id: str,
    registry: EvidenceRegistry,
    version_of: VersionOf,
    cfg: ChatAgentConfig,
) -> Comparison:
    """같은 피치 안의 두 Take를 한 지표로 비교한다(6-4). `prev`가 앞(이전), `cur`가 뒤(지금)다.

    Args:
        prev, cur: 비교할 두 Take 스냅샷.
        metric: 지표 이름. 방향 표(`cfg.direction`)에서 방향이 `HIGHER` · `LOWER` · `RANGE`여야 비교한다.
        slide_number: 장 번호(None이면 Take 값).
        pitch_id: 근거에 적을 피치. **호출한 쪽(도구)이 같은 피치 범위의 스냅샷만 넘겨야 한다**: `TakeSummary`에는
            피치 · 소유자 정보가 없어 이 함수가 다른 피치의 Take를 가려낼 수 없다(범위 밖 Take는 도구가 읽기 전에 막는다).
        registry: 근거 등록부. 값은 `RECORD`, 차이 · 거리 · 결과는 `COMPUTED`로 발급한다.
        version_of: 스냅샷 → 근거 지문용 `RecordVersion`.
        cfg: 설정.

    Returns:
        `Comparison`. 비교할 수 없으면 `comparable:false`와 `reason`:
        `NO_DIRECTION` · `NOT_COMPLETED_FULLY` · `MISSING_VERSION` · `DIFFERENT_SCRIPT_VERSION` ·
        `DIFFERENT_CRITERIA_VERSION`(평가 기준 `standard_version`) · `RULE_VERSION_CHANGED`(그 지표의 판정 기준 버전) ·
        `NO_VALUE` · `NO_RANGE` · `INVALID_DATA` · `OMITTED` · `NOT_LOADED`.
        예외를 던지지 않는다(값 검사 위반도 `INVALID_DATA` 결과다).

    예:
        Take 4의 청중 시선 0.28, Take 5의 0.33 → ``outcome="IMPROVED"``(HIGHER), ``previous=0.28``, ``current=0.33``.
        같은 값이면 ``SAME``. 범위 지표는 각 Take 범위 대비 거리로 가르고, 범위가 다르면 ``range_changed=True``.
    """

    def nc(reason: str) -> Comparison:
        return Comparison(comparable=False, reason=reason, metric=metric, slide_number=slide_number)

    direction = cfg.direction.get(metric)
    if direction is None:
        return nc("NO_DIRECTION")
    ps, cs = prev.summary, cur.summary
    if ps.completed_fully is not True or cs.completed_fully is not True:
        return nc("NOT_COMPLETED_FULLY")
    # 필수 버전 누락도 불일치로 본다(L4-08). 평가 기준 버전 standard_version 은 null 도 하나의 값이다
    if not ps.script_version_id or not cs.script_version_id:
        return nc("MISSING_VERSION")
    if ps.script_version_id != cs.script_version_id:
        return nc("DIFFERENT_SCRIPT_VERSION")
    if ps.standard_version != cs.standard_version:
        return nc("DIFFERENT_CRITERIA_VERSION")

    pr = read_metric(prev, metric, slide_number, cfg)
    cr = read_metric(cur, metric, slide_number, cfg)
    reason = _read_reason(pr, cr)
    if reason:
        return nc(reason)
    if pr.area != "CONTENT":  # 내용 전달 지표에는 코치 판정 기준 버전이 없다
        if not pr.rule_version or not cr.rule_version:
            return nc("MISSING_VERSION")
        if pr.rule_version != cr.rule_version:
            return nc("RULE_VERSION_CHANGED")

    p_range = c_range = None
    p_src: list[RangeSource] = []
    c_src: list[RangeSource] = []
    if direction == "RANGE":
        try:
            p_range, p_src = range_sources(prev, metric, slide_number, cfg)
            c_range, c_src = range_sources(cur, metric, slide_number, cfg)
        except InvalidDataError:
            return nc("INVALID_DATA")
        except OmittedDataError:
            return nc("OMITTED")
        if p_range is None or c_range is None:
            return nc("NO_RANGE")

    p_val, c_val = float(pr.value), float(cr.value)  # type: ignore[arg-type]
    p_dist = c_dist = None
    if p_range is not None and c_range is not None:
        p_dist = range_distance(p_val, *p_range)
        c_dist = range_distance(c_val, *c_range)

    outcome: Outcome
    if p_val == c_val:
        outcome = "SAME"
    elif direction == "RANGE":
        assert p_dist is not None and c_dist is not None
        if p_dist == 0 and c_dist == 0:
            outcome = "BOTH_IN_RANGE"
        elif c_dist < p_dist:
            outcome = "IMPROVED"
        elif c_dist > p_dist:
            outcome = "WORSENED"
        else:
            outcome = "SAME_DISTANCE"
    elif direction == "HIGHER":
        outcome = "IMPROVED" if c_val > p_val else "WORSENED"
    else:  # LOWER
        outcome = "IMPROVED" if c_val < p_val else "WORSENED"

    # 근거: 두 값(RECORD) → 차이 · 거리 · 결과(COMPUTED, 입력 근거를 따라 재계산 가능)
    unit = cfg.metric_units.get(metric)
    p_id = record_read_value(registry, pitch_id, prev, pr, cfg, version_of)
    c_id = record_read_value(registry, pitch_id, cur, cr, cfg, version_of)
    ids = [p_id, c_id]
    scope = f"{metric}" if slide_number is None else f"{metric}[slide_number={slide_number}]"
    diff = registry.computed(
        calc="DIFF", inputs=[p_id, c_id], value=c_val - p_val, pitch_id=pitch_id,
        path=f"computed.diff.{scope}", metric=metric, slide_number=slide_number, unit=unit,
        rule_version=cr.rule_version,
    )  # fmt: skip
    ids.append(diff.evidence_id)
    outcome_inputs = [p_id, c_id]
    if p_dist is not None and c_dist is not None:
        for tag, snap, rid, dist, srcs in (
            ("previous", prev, p_id, p_dist, p_src),
            ("current", cur, c_id, c_dist, c_src),
        ):
            # 범위 경계도 기록 근거로 발급해 거리 계산의 입력에 넣는다(경계를 모르면 거리를 다시 계산할 수 없다)
            bound_ids = [
                _record_bound(registry, pitch_id, snap, src, metric, slide_number, cfg, version_of)
                for src in srcs
            ]
            d_ev = registry.computed(
                calc="DISTANCE", inputs=[rid, *bound_ids], value=dist, pitch_id=pitch_id,
                path=f"computed.distance.{tag}.{scope}", metric=metric, slide_number=slide_number,
                unit=unit, rule_version=cr.rule_version,
            )  # fmt: skip
            ids += [*bound_ids, d_ev.evidence_id]
            outcome_inputs.append(d_ev.evidence_id)
    out_ev = registry.computed(
        calc="OUTCOME", inputs=outcome_inputs, value=outcome, pitch_id=pitch_id,
        path=f"computed.outcome.{scope}", metric=metric, slide_number=slide_number, unit=None,
        rule_version=cr.rule_version,
    )  # fmt: skip
    ids.append(out_ev.evidence_id)

    return Comparison(
        comparable=True,
        reason=None,
        metric=metric,
        slide_number=slide_number,
        previous=p_val,
        current=c_val,
        outcome=outcome,
        previous_range=_rng(p_range) if p_range else None,
        current_range=_rng(c_range) if c_range else None,
        range_changed=(p_range != c_range) if p_range and c_range else None,
        previous_distance=p_dist,
        current_distance=c_dist,
        evidence_ids=ids,
    )


# ── 목표 판정 ─────────────────────────────────────────────────────────────

GoalVerdict = Literal["ACHIEVED", "NOT_ACHIEVED", "UNKNOWN", "BASELINE_MISMATCH"]


@dataclass
class GoalJudgement:
    """목표 판정 한 번의 결과.

    - `status`: `ACHIEVED` · `NOT_ACHIEVED`(끝) / `BASELINE_MISMATCH`(끝, 새 목표 필요) / `UNKNOWN`(끝내지 않고 다음 Take를 기다림).
    - `terminal`: 목표를 끝내는 판정인가(`UNKNOWN`만 False).
    - `observed`: 지금 Take의 값(없으면 None).
    - `reason_code`: `GOAL_RULE_CHANGED`(목표의 `goal_rule_version`이 지금 설정의 판과 다름: `UNKNOWN`으로 두고 끝내지 않으며
      호출한 쪽이 새 목표를 정하자고 안내한다) 외에, 끝내지 못했거나 버전이 달랐던 까닭(`DIFFERENT_SCRIPT_VERSION` · `DIFFERENT_CRITERIA_VERSION` ·
      `RULE_VERSION_CHANGED` · `NOT_COMPLETED_FULLY` · `NO_VALUE` · `INVALID_DATA` · `OMITTED` · `NOT_LOADED`).
      이룸 · 못 이룸이면 None.
    - `evidence_ids`: 지금 값(RECORD)과 계산 결과(COMPUTED).
    """

    status: GoalVerdict
    terminal: bool
    observed: float | None
    reason_code: str | None
    evidence_ids: list[str] = field(default_factory=list)


def _goal_record(
    registry: EvidenceRegistry, goal: Goal, name: str, value: float | str, cfg: ChatAgentConfig
) -> str:
    """목표에 고정한 조건 하나를 근거로 발급한다(`kind: GOAL`). 목표 판 · 지표 · 장이 경로에 들어 다른 목표와 섞이지 않는다."""
    slide = "" if goal.slide_number is None else f"[slide_number={goal.slide_number}]"
    ev = registry.record(
        pitch_id=goal.pitch_id,
        # 기준 Take에 매단다: 기준 Take가 지워지면(drop_sources) 이 조건과 그것을 입력으로 쓴 계산 근거도 함께 무효다
        take_id=goal.base.take_id,
        take_number=goal.base.take_number,
        kind="GOAL",
        revision=goal.version,
        path=f"conversation_goal.v{goal.version}.{goal.metric}{slide}.{name}",
        value=value,
        metric=goal.metric,
        slide_number=goal.slide_number,
        unit=cfg.metric_units.get(goal.metric),
    )
    return ev.evidence_id


def judge_goal(
    goal: Goal,
    take: TakeSnapshot,
    *,
    registry: EvidenceRegistry,
    version_of: VersionOf,
    cfg: ChatAgentConfig,
) -> GoalJudgement:
    """시작한 Take 하나로 목표를 판정한다(6-4 목표 판정 · 판정 순서).

    Args:
        goal: 저장된 목표. 지표 · 장 · operator · `min_delta` · 기준 값 · 범위 · 버전을 **그대로** 쓴다.
            설정(`cfg`)이 바뀌어도 같은 목표 · 같은 Take는 같은 결과다(L4-12). `goal.goal_rule_version`의 계산식 판
            (`+` 앞부분)이 `SUPPORTED_GOAL_ALGORITHMS`에 없으면 판정하지 않고 `UNKNOWN`(`GOAL_RULE_CHANGED`)으로 두며,
            호출한 쪽이 `SystemText.GOAL_RULE_CHANGED`로 새 목표를 정하자고 안내한다.
        take: 판정할 Take 스냅샷.
        registry, version_of, cfg: `compare_takes`와 같다. `cfg`는 값 읽기 규칙(단위 · 최소 채점 비율)에만 쓴다.

    Returns:
        `GoalJudgement`. 순서: ① 필수 버전(목표와 Take의 `script_version_id`)이 없으면 `UNKNOWN`(`INVALID_DATA`)
        ② 대본 버전 · 평가 기준 버전(`standard_version`은 null도 값으로 비교) · 그 지표의 판정 기준 버전이 목표와
        다르면 `BASELINE_MISMATCH`(끝). 판정 기준 버전은 지표 값이 생략돼도 읽을 수 있으면 먼저 비교한다
        ③ 값이 규칙을 어기거나 지표 판정 기준 버전이 없으면 `UNKNOWN`(`INVALID_DATA`) ④ `completed_fully`가 아니거나
        값이 없으면 `UNKNOWN` ⑤ 이룸 · 못 이룸. `gt_base`: ``현재 - 기준값 >= min_delta``, `lt_base`: ``기준값 - 현재
        >= min_delta``, `in_range`: 고정한 `base.range`로 ``d(현재) == 0``. 예외를 던지지 않는다.
        근거: 현재 값 · 목표의 고정 조건(`GOAL`) → 계산(`GOAL_DELTA` · `GOAL_DISTANCE`) → 결과(`GOAL_RESULT`).
    """
    if goal.goal_rule_version.split("+")[0] not in SUPPORTED_GOAL_ALGORITHMS:
        return GoalJudgement("UNKNOWN", False, None, "GOAL_RULE_CHANGED")
    s = take.summary
    # 필수 버전 누락은 불일치가 아니라 계산을 멈추는 오류다. 유효한 버전끼리만 비교한다
    if not s.script_version_id or not goal.script_version_id:
        return GoalJudgement("UNKNOWN", False, None, "INVALID_DATA")
    if s.script_version_id != goal.script_version_id:
        return GoalJudgement("BASELINE_MISMATCH", True, None, "DIFFERENT_SCRIPT_VERSION")
    if s.standard_version != goal.standard_version:
        return GoalJudgement("BASELINE_MISMATCH", True, None, "DIFFERENT_CRITERIA_VERSION")

    rv = read_metric(take, goal.metric, goal.slide_number, cfg)
    if rv.state == "NOT_LOADED":
        return GoalJudgement("UNKNOWN", False, None, rv.state)
    # 버전 차이는 값 검사 · 생략보다 먼저 본다(값이 빠져도 판 불일치면 이 목표는 끝난다, 6-4 판정 순서)
    if rv.rule_version and rv.rule_version != goal.base.metric_rule_version:
        return GoalJudgement("BASELINE_MISMATCH", True, None, "RULE_VERSION_CHANGED")
    if rv.state == "OMITTED":
        return GoalJudgement("UNKNOWN", False, None, "OMITTED")
    if rv.state == "INVALID_DATA" or not rv.rule_version:
        # 값이 규칙을 어겼거나 필수 버전이 없다: 불일치가 아니라 계산을 멈추고 목표는 끝내지 않는다
        return GoalJudgement("UNKNOWN", False, None, "INVALID_DATA")
    if s.completed_fully is not True:
        return GoalJudgement("UNKNOWN", False, None, "NOT_COMPLETED_FULLY")
    if rv.state != "OK":
        return GoalJudgement("UNKNOWN", False, None, "NO_VALUE")  # 값 없음은 실패가 아니다

    current = float(rv.value)  # type: ignore[arg-type]
    base_value = float(goal.base.value)
    cond: list[tuple[str, float | str]]
    amount: float
    if goal.operator == "in_range":
        rng = goal.base.range
        if rng is None or (rng.low is None and rng.high is None):
            return GoalJudgement("UNKNOWN", False, current, "INVALID_DATA")
        amount = range_distance(current, rng.low, rng.high)
        calc = "GOAL_DISTANCE"
        achieved = amount == 0
        cond = [("operator", goal.operator)] + [
            (n, float(v))
            for n, v in (("base.range.low", rng.low), ("base.range.high", rng.high))
            if v is not None
        ]
    else:
        delta = goal.min_delta
        if delta is None or not math.isfinite(float(delta)) or delta <= 0:
            return GoalJudgement("UNKNOWN", False, current, "INVALID_DATA")
        amount = current - base_value if goal.operator == "gt_base" else base_value - current
        calc = "GOAL_DELTA"
        achieved = amount + _EPS >= float(delta)
        # operator 도 입력 근거로 둔다: 뺄셈 방향(gt_base: 현재 - 기준 / lt_base: 기준 - 현재)을 입력만으로 복원하려면 필요하다
        cond = [
            ("operator", goal.operator),
            ("base.value", base_value),
            ("min_delta", float(delta)),
        ]

    rec_id = record_read_value(registry, goal.pitch_id, take, rv, cfg, version_of)
    cond_ids = [_goal_record(registry, goal, name, v, cfg) for name, v in cond]
    slide = "" if goal.slide_number is None else f"[slide_number={goal.slide_number}]"
    tag = f"v{goal.version}.{goal.metric}{slide}"
    named = list(zip((n for n, _ in cond), cond_ids, strict=True))
    comp = registry.computed(
        calc=calc,
        inputs=[rec_id, *(cid for n, cid in named if n != "min_delta")],
        value=amount,
        pitch_id=goal.pitch_id,
        path=f"computed.goal.{tag}",
        metric=goal.metric,
        slide_number=goal.slide_number,
        unit=cfg.metric_units.get(goal.metric),
        rule_version=rv.rule_version,
    )
    status = "ACHIEVED" if achieved else "NOT_ACHIEVED"
    result = registry.computed(
        calc="GOAL_RESULT",
        inputs=[comp.evidence_id, *(cid for n, cid in named if n == "min_delta")],
        value=status,
        pitch_id=goal.pitch_id,
        path=f"computed.goal_result.{tag}",
        metric=goal.metric,
        slide_number=goal.slide_number,
        unit=None,
        rule_version=rv.rule_version,
    )
    return GoalJudgement(
        status, True, current, None, [rec_id, *cond_ids, comp.evidence_id, result.evidence_id]
    )


# ── 목표 초안 ─────────────────────────────────────────────────────────────


def build_goal_draft(
    base_snapshot: TakeSnapshot,
    metric: str,
    slide_number: int | None,
    *,
    pitch_id: str,
    cfg: ChatAgentConfig,
) -> GoalDraft | str:
    """기준 Take의 실제 기록에서 목표 초안을 만든다(6-4 · L4-11). 모델이 준 기준값 · 범위 · operator는 받지 않는다.

    Args:
        base_snapshot: 목표 기준이 될 Take(보통 지금 보던 Take).
        metric: 목표 지표. `cfg.goal_metrics`의 네 개(`audience_ratio` · `filler_per_min` · `cpm` · `duration_ms`)만.
        slide_number: 장 번호(None이면 Take 값).
        pitch_id: 목표의 피치.

    Returns:
        `GoalDraft`(operator는 설정의 첫 허용값, `gt_base` · `lt_base`는 설정의 `min_delta`를 목표에 **고정**,
        `in_range`는 `range_for`의 범위를 `base.range`로 고정하고 `min_delta`는 null,
        `goal_rule_version`은 `cfg.goal_rule_version()`). 못 만들면 오류 코드 문자열:
        `UNMEASURABLE_METRIC`(허용 밖 지표) · `NO_VALUE`(기준 값 없음) · `NO_RANGE`(범위를 못 찾음) ·
        `INVALID_DATA`(값 · 범위 위반, 또는 설정이 규칙을 어김) · `NO_VERSION`(판정 기준 버전 · 결과 판 없음).

    예:
        build_goal_draft(take4, "audience_ratio", 3, pitch_id="p1", cfg=cfg) →
        ``GoalDraft(operator="gt_base", min_delta=0.05, base.value=0.28, ...)``
    """
    gm = cfg.goal_metrics.get(metric)
    if gm is None or not gm.operators:
        return "UNMEASURABLE_METRIC"
    rv = read_metric(base_snapshot, metric, slide_number, cfg)
    if rv.state == "INVALID_DATA":
        return "INVALID_DATA"
    if rv.state != "OK":
        return "NO_VALUE"
    if not rv.rule_version or rv.revision is None or not base_snapshot.summary.script_version_id:
        return "NO_VERSION"  # 대본 버전이 없는 기록은 목표 판정에서 필수 버전 누락이라 목표를 만들지 않는다
    operator = gm.operators[0]
    base_range: Range | None = None
    min_delta: float | None = None
    if operator == "in_range":
        try:
            rng = range_for(base_snapshot, metric, slide_number, cfg)
        except InvalidDataError:
            return "INVALID_DATA"
        except OmittedDataError:
            return "NO_VALUE"
        if rng is None:
            return "NO_RANGE"
        base_range = Range(low=rng[0], high=rng[1])
    else:
        min_delta = gm.min_delta
        if min_delta is None or not math.isfinite(min_delta) or min_delta <= 0:
            return "INVALID_DATA"
    s = base_snapshot.summary
    try:
        return GoalDraft.model_validate(
            {
                "pitch_id": pitch_id,
                "metric": metric,
                "slide_number": slide_number,
                "operator": operator,
                "script_version_id": s.script_version_id,
                "standard_version": s.standard_version,
                "goal_rule_version": cfg.goal_rule_version(),
                "base": {
                    "take_id": s.take_id,
                    "take_number": s.take_number,
                    "kind": rv.kind,
                    "revision": rv.revision,
                    "value": rv.value,
                    "metric_rule_version": rv.rule_version,
                    "range": base_range.model_dump() if base_range else None,
                },
                "min_delta": min_delta,
            }
        )
    except ValueError:
        return "INVALID_DATA"
