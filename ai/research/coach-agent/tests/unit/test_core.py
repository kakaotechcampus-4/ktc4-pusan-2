"""엔진 — 실패해도 발표를 방해하지 않는다."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from coach import core as core_mod
from coach import decide, decide_safe

from .conftest import make_request
from .fakes import fake_judges


def test_internal_error_returns_wait_and_previous_state(monkeypatch: pytest.MonkeyPatch):
    judges = fake_judges()
    first = decide(make_request(1000), judges)

    def boom(*args):  # noqa: ARG001
        raise RuntimeError("tick bug")

    monkeypatch.setattr(core_mod.measure, "build_tick", boom)
    resp = decide_safe(make_request(2000, state=first.coach_state), judges)
    assert resp.action.value == "WAIT"
    assert resp.reason_codes == ["INTERNAL_ERROR"]
    assert resp.coach_state == first.coach_state
    assert resp.feedback is None


def test_internal_error_on_first_tick_still_returns_a_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(core_mod.measure, "build_tick", lambda *a: 1 / 0)
    resp = decide_safe(make_request(1000), fake_judges())
    assert resp.coach_state["v"] == 1


def test_bad_request_is_not_swallowed():
    """형식 오류는 API 가 422 로 바꾼다. 코치가 삼키면 BE 가 원인을 모른다."""
    with pytest.raises(ValidationError):
        decide_safe({"take_id": "x"}, fake_judges())


def test_decide_does_not_mutate_the_request_state():
    judges = fake_judges()
    first = decide(make_request(1000), judges)
    snapshot = dict(first.coach_state)
    decide(make_request(2000, state=first.coach_state), judges)
    assert first.coach_state == snapshot


def test_indicators_are_the_states_the_modules_reported():
    resp = decide(make_request(1000), fake_judges())
    assert resp.indicators == {
        "GAZE": "NORMAL",
        "SPEED": "NORMAL",
        "VOLUME": "NORMAL",
        "PAUSE": "NORMAL",
        "FILLER": "NORMAL",
        "TIME": "UNKNOWN",  # 계획이 없어 시간은 잴 수 없다
    }
