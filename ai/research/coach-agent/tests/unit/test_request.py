"""요청 계약 — 모르는 필드는 무시하고, 코칭 계획 · 반복 문제는 매 요청에서 읽는다."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from coach.schemas import CoachRequest

from .conftest import Session, gaze_on, make_request

RELAX_GAZE = {"focus": [], "relax": [{"area": "GAZE", "slide_number": 1}]}


def test_unknown_request_fields_are_ignored():
    plan = {"focus": [{"area": "GAZE", "weight": 1.5}], "relax": []}
    plain = make_request(10_000, coaching_plan=plan)
    noisy = {
        **plain,
        "schema_version": "1.2",
        "memory": {"recurring_issues": [{"area": "GAZE", "slide_number": 1}]},
        "coaching_plan": {**plan, "unknown": 1},
        "extra": {"a": 1},
    }
    # 모르는 필드는 검증된 요청에 남지 않는다 — 코치가 읽는 것은 검증된 요청뿐이다
    assert CoachRequest.model_validate(noisy) == CoachRequest.model_validate(plain)


def test_why_and_source_in_the_plan_do_not_change_decisions():
    focus = {"area": "GAZE", "weight": 1.8}
    noisy = {"source": "LLM", "focus": [{**focus, "why": "근거"}], "relax": []}
    bare = {"focus": [focus]}
    with_why = Session(coaching_plan=noisy).run(10_000, 13_000, **gaze_on(0.75))
    without = Session(coaching_plan=bare).run(10_000, 13_000, **gaze_on(0.75))
    assert "PLAN_FOCUS" in with_why[-1].reason_codes  # 계획이 실제로 쓰인 판단을 비교한다
    assert with_why == without


def test_coaching_plan_is_read_from_every_request():
    s = Session()
    s.state = None
    first = s.step(10_000, coaching_plan=RELAX_GAZE, **gaze_on(0.9))
    assert "PLAN_RELAXED" in {r for c in s.candidates_of(first) for r in c.reasons}
    assert "plan" not in first.coach_state
    # 이후 요청이 null 을 보내면 첫 요청의 봐주기가 남아 있지 않다
    later = [s.step(t, coaching_plan=None, **gaze_on(0.9)) for t in range(11_000, 15_000, 1_000)]
    assert all("PLAN_RELAXED" not in c.reasons for r in later for c in s.candidates_of(r))
    assert any(r.feedback is not None for r in later)


def test_recurring_issues_from_the_request_give_recurring():
    recurring = [{"area": "GAZE", "slide_number": 1}]
    plain = Session().run(10_000, 13_000, **gaze_on(0.75))[-1]
    resp = Session(recurring_issues=recurring).run(10_000, 13_000, **gaze_on(0.75))[-1]
    assert "RECURRING" in resp.reason_codes and "RECURRING" not in plain.reason_codes


@pytest.mark.parametrize("value", [True, False, None])
def test_script_used_is_accepted(value):
    assert CoachRequest.model_validate(make_request(1_000, script_used=value)).script_used is value


@pytest.mark.parametrize("value", [False, None])
def test_script_not_used_does_not_change_decisions(value):
    base = Session().run(10_000, 13_000, **gaze_on(0.9))
    assert Session(script_used=value).run(10_000, 13_000, **gaze_on(0.9)) == base


def test_eq_operator_is_rejected():
    mission = {
        "mission_id": "m1",
        "area": "GAZE",
        "slide_number": 1,
        "target": {"metric": "script_ratio", "operator": "EQ", "value": 0.3},
    }
    with pytest.raises(ValidationError):
        CoachRequest.model_validate(make_request(1_000, missions=[mission]))
