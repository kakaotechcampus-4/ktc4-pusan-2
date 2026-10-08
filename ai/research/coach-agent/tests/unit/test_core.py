"""엔진 — 실패해도 발표를 방해하지 않는다."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from coach import decide, decide_safe
from coach import core as core_mod

from .conftest import gaze_script, make_request


def test_internal_error_returns_wait_and_previous_state(monkeypatch: pytest.MonkeyPatch):
    first = decide(make_request(1000))

    def boom(tick):  # noqa: ARG001
        raise RuntimeError("evaluator bug")

    monkeypatch.setattr(core_mod, "run_all", boom)
    resp = decide_safe(make_request(2000, state=first.coach_state, gaze=gaze_script(0.9)))
    assert resp.action.value == "WAIT"
    assert resp.reason_codes == ["INTERNAL_ERROR"]
    assert resp.coach_state == first.coach_state
    assert resp.feedback is None


def test_internal_error_on_first_tick_still_returns_a_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(core_mod, "run_all", lambda tick: 1 / 0)
    resp = decide_safe(make_request(1000))
    assert resp.coach_state["v"] == 1


def test_bad_request_is_not_swallowed():
    """형식 오류는 API 가 422 로 바꾼다. 코치가 삼키면 BE 가 원인을 모른다."""
    with pytest.raises(ValidationError):
        decide_safe({"take_id": "x"})


def test_decide_does_not_mutate_the_request_state():
    first = decide(make_request(1000))
    snapshot = dict(first.coach_state)
    decide(make_request(2000, state=first.coach_state, gaze=gaze_script(0.9)))
    assert first.coach_state == snapshot
