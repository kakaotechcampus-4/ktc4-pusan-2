"""한 턴 전체를 보는 마지막 검사(가드레일) 시험 (Issue #1194 화면 검토, 기능 문서 6-9 '한 턴 전체 검사' · 11-5).

- 같은 사실의 반복(`DUP_FACT`): 근거 id 와 값이 모두 그 턴에 이미 나간 문장의 것이면 뺀다. 새 근거 · 새 값이 하나라도 있으면 남긴다.
- 기준 없는 '확인 불가'(`UNVERIFIABLE_NO_CRITERION`): 값은 도구 결과에 있고 리뷰 판정 · 문제 구간 · 대화 목표가 없다고 확인됐을 때만
  고정 문장으로 바꾼다. 기준이 있거나 모르면 바꾸지 않고 결정 로그에만 남긴다.
- 흘려보낸 문장 줄과 끝 줄 답이 같다(뺀 문장은 둘 다에 없다). 숫자 검사는 그대로다.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent import templates
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG as CFG
from pitch_coach_ai.features.chat_agent.evidence import EvidenceRegistry
from pitch_coach_ai.features.chat_agent.output_guard import OutputGuard, values_of
from pitch_coach_ai.features.chat_agent.schemas import Claim

from . import fakes as F
from .test_plan_progress_turn import decisions
from .test_stream_answer import assert_prefix_invariant, compare_answer, request, script

pytestmark = pytest.mark.output_guard


def _lp(reg: EvidenceRegistry, goal: Any = None) -> Any:
    logged: list[tuple[str, list[str], str]] = []
    lp = SimpleNamespace(
        ctx=SimpleNamespace(registry=reg, goal=goal),
        cfg=CFG,
        st=SimpleNamespace(emitted=[]),
        logged=logged,
    )
    lp._decide = lambda signal, facts, effect, decider: logged.append((signal, facts, effect))
    return lp


def _ev(reg: EvidenceRegistry, path: str, value: Any, metric: str | None = None, unit: str | None = None) -> str:
    return reg.record(
        pitch_id="p-1", take_id="t-5", take_number=5, kind="TAKE_RESULT", revision=1,
        path=path, value=value, metric=metric, unit=unit,
    ).evidence_id  # fmt: skip


def test_values_are_compared_as_number_and_unit() -> None:
    assert values_of("Take 9의 대본 응시는 32%예요.") == {(32.0, "%")}
    assert values_of("3장 말 속도는 310자/분이에요.") == values_of("310자/분")


def test_repeated_fact_is_dropped_but_a_new_evidence_or_value_is_kept() -> None:
    reg = EvidenceRegistry("run")
    ratio = _ev(reg, "take_result.metrics.script_ratio", 0.32, "script_ratio", "ratio")
    seg = _ev(reg, "problem_segments[area=GAZE,slide_number=7].count", 1)
    g = OutputGuard(_lp(reg))
    first = Claim(kind="RECORD", text="Take 9의 대본 응시는 32%로 기록됐어요.", evidence_ids=[ratio])
    assert g.admit(first, []) is first
    g.note(first, [])
    second = Claim(kind="RECORD", text="Take 9의 대본 응시는 32%이고 7장에 대본 응시 구간이 있어요.", evidence_ids=[ratio, seg])
    assert g.admit(second, []) is second  # 새 근거(7장 문제 구간)
    g.note(second, [])
    third = Claim(kind="RECORD", text="Take 9의 대본 응시는 32%예요.", evidence_ids=[ratio])
    assert g.admit(third, []) is None  # 근거 · 값 모두 이미 나감
    other_value = Claim(kind="RECORD", text="Take 9의 대본 응시는 33%예요.", evidence_ids=[ratio])
    assert g.admit(other_value, []) is other_value  # 새 값
    no_value = Claim(kind="RECORD", text="Take 9의 대본 응시 기록을 다시 봤어요.", evidence_ids=[ratio])
    assert g.admit(no_value, []) is no_value  # 값이 없는 문장은 빼지 않는다
    g.log()
    assert g.lp.logged == [("OUTPUT_CHECK", ["DUP_FACT"], "DROPPED")]


def _unverifiable(reg: EvidenceRegistry) -> tuple[Claim, list[dict[str, Any]]]:
    a = {
        "assessment_id": "a-1",
        "verdict": "UNVERIFIABLE",
        "evidence_ids": [],
        "subject": {"dimension": "script_independence", "metric": "script_ratio", "take_ids": ["t-5"]},
    }
    claim = Claim(
        kind="ASSESSMENT",
        text=templates.UNVERIFIABLE.format(quote="대본 응시 비율이 너무 높은거같아"),
        assessment_id="a-1",
    )
    return claim, [a]


def test_unverifiable_with_value_and_no_criterion_becomes_the_fixed_sentence() -> None:
    reg = EvidenceRegistry("run")
    _ev(reg, "take_result.metrics.script_ratio", 0.32, "script_ratio", "ratio")
    _ev(reg, "review.status", "NONE")
    _ev(reg, "problem_segments[area=GAZE].count", 0)
    g = OutputGuard(_lp(reg))
    claim, a = _unverifiable(reg)
    got = g.admit(claim, a)
    assert got is not None and got.kind == "SYSTEM"
    assert got.text == templates.SystemText.VALUE_NO_CRITERION.value
    assert got.text.endswith("요.")


@pytest.mark.parametrize("criterion", ["segments", "review", "goal", "unknown"])
def test_unverifiable_with_a_criterion_or_unknown_is_left_and_noted(criterion: str) -> None:
    reg = EvidenceRegistry("run")
    _ev(reg, "take_result.metrics.script_ratio", 0.32, "script_ratio", "ratio")
    goal = None
    if criterion == "segments":
        _ev(reg, "review.status", "NONE")
        _ev(reg, "problem_segments[area=GAZE,slide_number=7].count", 1)
    elif criterion == "review":
        _ev(reg, "review.dimensions.script_independence", "PRIORITY")
    elif criterion == "goal":
        _ev(reg, "review.status", "NONE")
        _ev(reg, "problem_segments[area=GAZE].count", 0)
        goal = SimpleNamespace(metric="script_ratio")
    g = OutputGuard(_lp(reg, goal))
    claim, a = _unverifiable(reg)
    assert g.admit(claim, a) is claim
    g.log()
    code = "UNVERIFIABLE_CRITERION_UNKNOWN" if criterion == "unknown" else "UNVERIFIABLE_HAS_CRITERION"
    assert g.lp.logged == [("OUTPUT_CHECK", [code], "NOTED")]


def test_turn_drops_the_repeated_comparison_and_keeps_lines_and_final_the_same() -> None:
    """판단 첫 문장이 이미 'Take 4 31% → Take 5 28%, 나빠졌어요'를 말했으면, 같은 근거 · 값의 비교 문장 · 값 문장은 내보내지 않는다.
    흘려보낸 문장 줄과 끝 줄 답의 앞부분이 같다(5-4)."""
    log: list[dict[str, Any]] = [{"event": "test_start"}]
    _, lines, data = F.run_turn(request(), F.FakeLlm(script(compare_answer())), log=log)
    texts = [c["text"] for c in data["message"]["claims"]]
    assert len(texts) == 1 and texts[0].startswith("말씀하신")
    assert_prefix_invariant(lines, data)
    assert [d[:3] for d in decisions(log) if d[0] == "OUTPUT_CHECK"] == [("OUTPUT_CHECK", "DROPPED", "CODE")] * 2
