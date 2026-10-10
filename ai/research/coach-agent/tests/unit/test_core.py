"""엔진 — 실패해도 발표를 방해하지 않는다."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from coach import core as core_mod
from coach import decide, decide_safe
from coach import judges as judges_mod
from coach.version import STATE_VERSION

from .conftest import PLAN, make_request
from .fakes import fake_judges

MODULES = ("gaze", "pace", "volume", "filler", "timing")


def _boom(*args):  # noqa: ARG001
    raise RuntimeError("tick bug")


def test_internal_error_skips_the_window_and_moves_on(monkeypatch: pytest.MonkeyPatch):
    judges = fake_judges()
    first = decide(make_request(1000, plan=PLAN), judges)
    before = first.coach_state

    monkeypatch.setattr(core_mod.measure, "build_tick", _boom)
    req = make_request(4000, plan=PLAN, state=before)
    resp = decide_safe(req, judges)
    assert resp.action.value == "WAIT"
    assert resp.reason_codes == ["INTERNAL_ERROR"]
    assert resp.feedback is None and resp.events == []
    assert resp.meta.criteria_versions["coach"].startswith("coach-1.2+")

    after = resp.coach_state
    # 모든 모듈의 커서가 이번 창 끝으로 넘어간다
    assert {n: after["cursors"][n]["since_ms"] for n in MODULES} == dict.fromkeys(MODULES, 4000)
    # 그 3초는 잴 수 없던 시간으로 total_ms (timing 은 elapsed_ms) 에만 더해진다
    for area in ("GAZE", "SPEED", "VOLUME", "PAUSE", "FILLER"):
        assert after["totals"][area] == {"total_ms": before["totals"][area]["total_ms"] + 3000}
    assert after["totals"]["TIME"]["elapsed_ms"] == before["totals"]["TIME"]["elapsed_ms"] + 3000
    assert after["totals"]["TIME"]["planned_ms"] == before["totals"]["TIME"]["planned_ms"]
    assert after["slide_totals"]["1"]["GAZE"] == {"total_ms": 4000}
    # 센 구간에 들어간다
    assert after["covered"] == [[0, 4000]]
    assert after["last_t_ms"] == 4000

    # 같은 요청이 다시 오면 처리한 시각이라 STALE_TICK 이고 아무것도 바뀌지 않는다
    monkeypatch.undo()
    again = decide_safe(make_request(4000, plan=PLAN, state=after), judges)
    assert again.reason_codes == ["STALE_TICK"] and again.coach_state == after
    # 다음 요청은 이어서 센다
    nxt = decide(make_request(5000, plan=PLAN, state=after), judges)
    assert nxt.coach_state["totals"]["GAZE"]["total_ms"] == 5000
    assert nxt.coach_state["cursors"]["gaze"]["since_ms"] == 5000


def test_repeated_internal_error_does_not_repeat_on_the_next_second(
    monkeypatch: pytest.MonkeyPatch,
):
    """같은 입력에서 되풀이되는 예외라도 시간이 지나 있다: 둘째 오류도 그 1초만 넘긴다."""
    judges = fake_judges()
    state = decide(make_request(1000), judges).coach_state
    monkeypatch.setattr(core_mod.measure, "build_tick", _boom)
    for t in (2000, 3000):
        resp = decide_safe(make_request(t, state=state), judges)
        assert resp.reason_codes == ["INTERNAL_ERROR"]
        state = resp.coach_state
    assert state["cursors"]["gaze"]["since_ms"] == 3000
    assert state["totals"]["GAZE"] == {"total_ms": 3000}


def test_internal_error_with_a_reset_state_says_so(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(core_mod.measure, "build_tick", _boom)
    resp = decide_safe(make_request(2000, state={"v": STATE_VERSION - 1}), fake_judges())
    assert resp.reason_codes == ["INTERNAL_ERROR", "STATE_RESET"]
    assert resp.coach_state["v"] == STATE_VERSION
    assert resp.coach_state["cursors"]["gaze"]["since_ms"] == 2000


def test_internal_error_when_skipping_fails_returns_the_received_state(
    monkeypatch: pytest.MonkeyPatch,
):
    judges = fake_judges()
    first = decide(make_request(1000), judges)
    monkeypatch.setattr(core_mod.measure, "build_tick", _boom)
    monkeypatch.setattr(judges_mod, "skip", _boom)
    resp = decide_safe(make_request(2000, state=first.coach_state), judges)
    assert resp.reason_codes == ["INTERNAL_ERROR"]
    assert resp.coach_state == first.coach_state
    fresh = decide_safe(make_request(2000), judges)
    assert fresh.coach_state["v"] == STATE_VERSION


def test_internal_error_keeps_the_last_criteria_versions(monkeypatch: pytest.MonkeyPatch):
    """예외 뒤 건너뛴 결과는 모듈이 낸 것이 아니라 기준 버전을 바꾸지 않는다."""
    judges = fake_judges()
    original = core_mod.measure.build_tick
    monkeypatch.setattr(core_mod.measure, "build_tick", _boom)
    first = decide_safe(make_request(1000), judges)
    assert first.meta.criteria_versions.keys() == {"coach"}
    monkeypatch.setattr(core_mod.measure, "build_tick", original)
    ok = decide_safe(make_request(2000, state=first.coach_state), judges)
    monkeypatch.setattr(core_mod.measure, "build_tick", _boom)
    again = decide_safe(make_request(3000, state=ok.coach_state), judges)
    assert again.meta.criteria_versions["gaze"] == "gaze-fake-1"
    assert again.coach_state["criteria_versions"]["gaze"] == "gaze-fake-1"


def test_internal_error_on_first_tick_still_returns_a_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(core_mod.measure, "build_tick", lambda *a: 1 / 0)
    resp = decide_safe(make_request(1000), fake_judges())
    assert resp.coach_state["v"] == STATE_VERSION


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
