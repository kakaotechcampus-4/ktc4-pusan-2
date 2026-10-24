"""t_ms 상한 · 큰 앞섬(T_MS_JUMP) · 새로 시작한 상태의 첫 요청(커서를 창 앞에서 시작)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from coach import core as core_mod
from coach import decide, decide_safe
from coach import judges as judges_mod
from coach.schemas import MAX_T_MS, MAX_T_MS_JUMP, CoachRequest, FinalizeRequest
from coach.state import dump_state, initial_state
from coach.version import STATE_VERSION

from .conftest import PLAN, make_request
from .fakes import fake_judges

MODULES = ("gaze", "pace", "volume", "filler")


def _boom(*args):  # noqa: ARG001
    raise RuntimeError("tick bug")


def test_t_ms_upper_bound_is_shared_by_evaluate_and_finalize():
    assert MAX_T_MS == 3_660_000 and MAX_T_MS_JUMP == 60_000
    assert CoachRequest.model_validate(make_request(MAX_T_MS)).t_ms == MAX_T_MS
    assert FinalizeRequest.model_validate({"take_id": "t", "t_ms": MAX_T_MS}).t_ms == MAX_T_MS
    with pytest.raises(ValidationError):
        CoachRequest.model_validate(make_request(MAX_T_MS + 1))
    with pytest.raises(ValidationError):
        FinalizeRequest.model_validate({"take_id": "t", "t_ms": MAX_T_MS + 1})


def test_jump_over_60_seconds_is_not_processed_and_returns_an_empty_state():
    judges = fake_judges()
    state = decide(make_request(91_000, plan=PLAN), judges).coach_state
    assert state["last_t_ms"] == 91_000

    # 정확히 60초 앞선 것까지는 처리한다
    ok = decide(make_request(151_000, plan=PLAN, state=state), judges)
    assert "T_MS_JUMP" not in ok.reason_codes and ok.coach_state["last_t_ms"] == 151_000

    jump = decide(make_request(151_001, plan=PLAN, state=state), judges)
    assert jump.action.value == "WAIT" and jump.reason_codes == ["T_MS_JUMP"]
    assert jump.events == [] and jump.indicators == {} and jump.feedback is None
    assert jump.coach_state == dump_state(initial_state())
    # 판정 모듈은 부르지 않는다
    assert len(judges.gaze.calls) == 2  # 91_000 · 151_000 두 번뿐


def test_jump_is_not_checked_without_a_processed_state():
    judges = fake_judges()
    # Take 첫 요청(coach_state null)
    first = decide(make_request(3_000_000, plan=PLAN), judges)
    assert "T_MS_JUMP" not in first.reason_codes and first.coach_state["last_t_ms"] == 3_000_000
    # 버전이 다른 state(STATE_RESET)
    reset = decide(make_request(3_000_000, state={"v": STATE_VERSION - 1}), judges)
    assert "T_MS_JUMP" not in reset.reason_codes and "STATE_RESET" in reset.reason_codes


def test_the_request_after_a_jump_starts_fresh_and_is_judged():
    judges = fake_judges()
    state = decide(make_request(91_000, plan=PLAN), judges).coach_state
    jump = decide(make_request(600_000, plan=PLAN, state=state), judges)
    assert jump.reason_codes == ["T_MS_JUMP"]

    nxt = decide(make_request(601_000, plan=PLAN, state=jump.coach_state), judges)
    assert "T_MS_JUMP" not in nxt.reason_codes and "STALE_TICK" not in nxt.reason_codes
    assert nxt.coach_state["last_t_ms"] == 601_000
    # 새로 시작했으므로 커서는 창 앞(571_000)에서 시작하고 그 뒤만 센다
    assert nxt.coach_state["covered"] == [[571_000, 601_000]]
    assert judges.gaze.calls[-1][0]["since_ms"] == 571_000


def _timing_spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    real = judges_mod.timing_judge

    def spy(inputs: dict[str, Any], t_ms: int, config: Any):
        seen.append(inputs)
        return real(inputs, t_ms, config)

    monkeypatch.setattr(judges_mod, "timing_judge", spy)
    return seen


def test_fresh_state_first_request_starts_cursors_at_the_window_start(
    monkeypatch: pytest.MonkeyPatch,
):
    seen = _timing_spy(monkeypatch)
    judges = fake_judges()
    resp = decide(make_request(100_000, plan=PLAN, slide=1, slide_started=10_000), judges)

    for name in MODULES:
        assert getattr(judges, name).calls[0][0]["since_ms"] == 70_000
    # 단어 커서는 그대로(단어는 60초 창으로 묶인다)
    assert judges.filler.calls[0][0]["words_since_ms"] == -1
    assert seen[0]["since_ms"] == 70_000
    # 센 구간은 창 앞부터
    assert resp.coach_state["covered"] == [[70_000, 100_000]]
    # 지금 장의 창 밖 몫(70_000 − 10_000)이 머문 시간에 미리 들어간다. STT 신뢰 시간에는 넣지 않는다
    assert seen[0]["slide_dwell_ms"]["1"] >= 60_000
    assert seen[0]["slide_stt_ok_ms"] == {}
    assert resp.coach_state["slide_dwell_offset_ms"] == {"1": 60_000}
    # 합계(Take 결과)에는 창 안의 30초만 센다
    assert resp.coach_state["totals"]["TIME"]["elapsed_ms"] == 30_000


def test_fresh_state_inside_the_first_window_has_no_offset(monkeypatch: pytest.MonkeyPatch):
    seen = _timing_spy(monkeypatch)
    judges = fake_judges()
    resp = decide(make_request(20_000, plan=PLAN, slide=1, slide_started=0), judges)
    assert judges.gaze.calls[0][0]["since_ms"] == 0
    assert seen[0]["slide_dwell_ms"] == {}
    assert (
        "slide_dwell_offset_ms" not in resp.coach_state
        or not resp.coach_state["slide_dwell_offset_ms"]
    )


def test_the_offset_is_added_to_dwell_on_later_requests(monkeypatch: pytest.MonkeyPatch):
    seen = _timing_spy(monkeypatch)
    judges = fake_judges()
    state = decide(
        make_request(100_000, plan=PLAN, slide=1, slide_started=10_000), judges
    ).coach_state
    decide(make_request(101_000, plan=PLAN, slide=1, slide_started=10_000, state=state), judges)
    # 센 30초 + 오프셋 60초
    assert seen[1]["slide_dwell_ms"] == {"1": 90_000}


def test_a_failure_on_the_first_fresh_request_also_starts_at_the_window_start(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(core_mod.measure, "build_tick", _boom)
    resp = decide_safe(
        make_request(100_000, plan=PLAN, slide=1, slide_started=10_000), fake_judges()
    )
    assert resp.reason_codes == ["INTERNAL_ERROR"]
    state = resp.coach_state
    # 건너뛴 시간도 창 앞(70_000)부터 센다
    assert state["covered"] == [[70_000, 100_000]]
    assert state["totals"]["GAZE"] == {"total_ms": 30_000}
    assert state["slide_dwell_offset_ms"] == {"1": 60_000}


def test_time_before_a_slide_that_started_inside_the_window_is_not_counted_for_it(
    monkeypatch: pytest.MonkeyPatch,
):
    # 지금 장이 창 안(90_000)에서 시작했으면 그 앞(70_000 ~ 90_000)은 어느 장인지 모른다 — 지금
    # 장의 머문 시간 · 장 합계에 넣지 않는다(Take 합계에는 넣는다)
    seen = _timing_spy(monkeypatch)
    judges = fake_judges()
    first = decide(make_request(100_000, plan=PLAN, slide=2, slide_started=90_000), judges)
    state = first.coach_state
    assert state["slide_unknown_before_ms"] == 90_000
    assert state["totals"]["TIME"]["elapsed_ms"] == 30_000
    assert state["slide_totals"]["2"]["TIME"]["elapsed_ms"] == 10_000
    decide(make_request(101_000, plan=PLAN, slide=2, slide_started=90_000, state=state), judges)
    assert seen[1]["slide_dwell_ms"] == {"2": 10_000}


def test_leftover_cursors_of_an_unprocessed_state_are_moved_to_the_window_start():
    # last_t_ms 가 없는데 커서가 남은 상태(모양은 맞는 state)도 창 앞에서 시작한다
    judges = fake_judges()
    raw = dump_state(initial_state())
    raw["cursors"] = {"gaze": {"since_ms": 0, "words_since_ms": -1}}
    resp = decide(make_request(100_000, plan=PLAN, state=raw), judges)
    assert judges.gaze.calls[0][0]["since_ms"] == 70_000
    assert resp.coach_state["covered"] == [[70_000, 100_000]]


def _run(slide: int, started: int, until: int = 6_000) -> dict[str, Any]:
    judges = fake_judges()
    state = None
    for t in range(1_000, until + 1, 1_000):
        state = decide(
            make_request(t, plan=PLAN, slide=slide, slide_started=started, state=state), judges
        ).coach_state
    return state


def test_first_slide_known_late_keeps_the_time_before_it_in_that_slide():
    # 첫 장(계획의 1번)이 400 에 시작한 것으로 처음 알려져도, 창 앞이 잘리지 않았으면 그 앞은 첫
    # 장의 몫이다 — 예전처럼 첫 장에 넣어 장 합계가 Take 합계와 같다(replay 도 첫 장을 0초로 싣는다)
    state = _run(slide=1, started=400)
    assert "slide_unknown_before_ms" not in state
    assert state["slide_totals"]["1"]["TIME"]["elapsed_ms"] == state["totals"]["TIME"]["elapsed_ms"]
    assert state["slide_totals"]["1"]["GAZE"]["total_ms"] == state["totals"]["GAZE"]["total_ms"]
    assert state["totals"]["TIME"]["elapsed_ms"] == 6_000


def test_slide_changed_before_the_first_request_leaves_the_earlier_time_unknown():
    # 1번 장 → 2번 장(400) 뒤 첫 요청: BE 는 지금 장(2번)만 싣는다. 그 앞 400 ms 는 1번 장의 몫이라
    # 2번 장에 넣지 않는다
    state = _run(slide=2, started=400)
    assert state["slide_unknown_before_ms"] == 400
    assert state["slide_totals"]["2"]["TIME"]["elapsed_ms"] == 5_600
    assert state["totals"]["TIME"]["elapsed_ms"] == 6_000


def test_first_slide_inside_a_cut_window_still_leaves_the_earlier_time_unknown():
    # 창 앞이 잘렸으면(t=100000 → start 70000) 첫 장이어도 그 앞(앞서 다른 장을 봤을 수 있음)은
    # 모르는 시간이다
    resp = decide(make_request(100_000, plan=PLAN, slide=1, slide_started=90_000), fake_judges())
    assert resp.coach_state["slide_unknown_before_ms"] == 90_000


def test_slide_started_exactly_at_the_window_start_has_no_offset_and_no_unknown():
    resp = decide(make_request(100_000, plan=PLAN, slide=2, slide_started=70_000), fake_judges())
    assert "slide_unknown_before_ms" not in resp.coach_state
    assert not resp.coach_state.get("slide_dwell_offset_ms")
