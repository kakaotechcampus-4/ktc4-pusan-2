"""이벤트 모양(§6 표)과 SLIDE — 장 방문의 영역별 합계를 단어가 다 확정된 뒤에 낸다."""

from __future__ import annotations

from typing import Any

import pytest

import coach.core as core_mod
from coach import decide_safe, finalize
from coach.schemas import (
    EpisodeEvent,
    InterventionEvent,
    OutcomeEvent,
    SlideEvent,
    StrategyEvent,
    SuppressedEvent,
)

from .conftest import PLAN, Session, gaze_on, make_request

# ── 이벤트 모양 ─────────────────────────────────────────────────────────

COMMON = {"kind", "event_id", "t_ms"}
TABLE: dict[type, set[str]] = {
    InterventionEvent: {
        "intervention_id",
        "issue_type",
        "area",
        "instruction",
        "message",
        "priority",
        "confidence",
        "reason_codes",
        "slide_number",
    },  # fmt: skip
    OutcomeEvent: {"intervention_id", "outcome", "metric", "before", "after"},
    EpisodeEvent: {
        "issue_type",
        "area",
        "slide_number",
        "start_ms",
        "end_ms",
        "peak_severity",
        "mean_severity",
        "intervention_ids",
        "suppressed_reasons",
        "reliable_ms",
        "unreliable_ms",
    },  # fmt: skip
    SlideEvent: {"slide_number", "start_ms", "end_ms", "target_ms", "tally"},
    StrategyEvent: {
        "issue_type",
        "area",
        "slide_number",
        "change",
        "from_instruction",
        "from_variant",
        "to_instruction",
        "to_variant",
        "intervention_id",
    },  # fmt: skip
    SuppressedEvent: {
        "issue_type",
        "area",
        "slide_number",
        "instruction",
        "status",
        "priority",
        "reasons",
    },  # fmt: skip
}


@pytest.mark.parametrize("cls", list(TABLE), ids=lambda c: c.__name__)
def test_each_event_kind_has_exactly_the_contract_fields(cls: type):
    assert set(cls.model_fields) == COMMON | TABLE[cls]


def test_suppressed_events_carry_the_slide_number(session: Session):
    session.run(10_000, 12_000, **gaze_on(0.9))  # 지속 시간이 모자라 후보가 기다린다
    suppressed = [e for e in session.events if e.kind == "SUPPRESSED"]
    assert suppressed and all(e.slide_number == 1 for e in suppressed)


# ── SLIDE ───────────────────────────────────────────────────────────────


def word(start_ms: int, end_ms: int) -> dict[str, Any]:
    return {"word": "가가가", "start_ms": start_ms, "end_ms": end_ms}


def slides_of(s: Session) -> list[SlideEvent]:
    return [e for e in s.events if e.kind == "SLIDE"]


def test_late_word_lands_in_the_visit_where_it_was_spoken_and_slide_waits_for_it():
    s = Session(plan=PLAN)
    s.run(1_000, 5_000, slide=1, slide_started=0, words=[word(4_000, 4_500)])
    # 2번 장으로 넘어갔다. 1번 장 단어는 아직 5.5초까지 확정되지 않았다
    s.run(6_000, 6_000, slide=2, slide_started=5_500)
    assert slides_of(s) == []
    visits = s.state["visits"]
    assert [(v["slide_number"], v["start_ms"], v.get("end_ms")) for v in visits] == [
        (1, 0, 5_500),
        (2, 5_500, None),
    ]
    # 1번 장에서 말했지만 7초에야 확정된 단어
    s.step(7_000, slide=2, slide_started=5_500, words=[word(5_200, 5_600)])
    (event,) = slides_of(s)
    assert (event.slide_number, event.start_ms, event.end_ms, event.target_ms) == (
        1,
        0,
        5_500,
        30_000,
    )
    assert event.event_id == "sl-1-0" and event.t_ms == 7_000
    assert event.tally["SPEED"]["chars"] == 6
    # 방문 합계는 그 장 번호 합계와 같다 (이 시점에 1번 장은 더 늘지 않는다)
    assert event.tally == s.state["slide_totals"]["1"]
    assert [v["slide_number"] for v in s.state["visits"]] == [2]


def test_revisit_makes_two_slide_events_while_slide_totals_merge():
    s = Session(plan=PLAN)
    slides = [(1, 0), (2, 3_000), (1, 6_000)]
    for t in range(1_000, 10_001, 1_000):
        number, started = [x for x in slides if x[1] < t][-1]
        s.step(t, slide=number, slide_started=started, words=[word(t - 900, t - 500)])
    first = slides_of(s)
    assert [(e.event_id, e.slide_number, e.start_ms, e.end_ms) for e in first] == [
        ("sl-1-0", 1, 0, 3_000),
        ("sl-2-3000", 2, 3_000, 6_000),
    ]
    fin = finalize(
        {"take_id": "test-take", "t_ms": 10_000, "coach_state": s.state}, s.judges, s.config
    )
    last = [e for e in fin.events if e.kind == "SLIDE"]
    assert [(e.event_id, e.slide_number, e.start_ms, e.end_ms) for e in last] == [
        ("sl-1-6000", 1, 6_000, 10_000)
    ]
    ones = [e for e in [*first, *last] if e.slide_number == 1]
    total = sum(e.tally["SPEED"]["chars"] for e in ones)
    assert total == s.state["slide_totals"]["1"]["SPEED"]["chars"]
    assert total == 3 * len(
        [t for t in range(1_000, 10_001, 1_000) if t - 900 < 3_000 or t - 900 >= 6_000]
    )
    assert last[0].target_ms == 30_000 and first[1].target_ms == 60_000


def test_pieces_before_the_first_slide_stay_out_of_its_visit():
    """장 정보가 5초에야 왔다: 그 전 몫은 장 번호별 합계에는 첫 장으로 들지만 방문에는 안 든다."""
    s = Session(plan=PLAN)
    for t in range(1_000, 5_000, 1_000):
        s.step(t, slide=None)
    s.step(6_000, slide=1, slide_started=5_000, words=[word(3_500, 3_800)])
    fin = finalize(
        {"take_id": "test-take", "t_ms": 6_000, "coach_state": s.state}, s.judges, s.config
    )
    (slide,) = [e for e in fin.events if e.kind == "SLIDE"]
    assert (slide.start_ms, slide.end_ms) == (5_000, 6_000)
    assert slide.tally["TIME"]["elapsed_ms"] == 1_000  # [5000, 6000) 만
    assert "chars" not in slide.tally.get("SPEED", {})  # 3.5초에 한 말은 장이 보이기 전
    totals = s.state["slide_totals"]["1"]
    assert totals["TIME"]["elapsed_ms"] == 2_000 and totals["SPEED"]["chars"] == 3


def test_target_ms_is_null_for_a_slide_outside_the_plan():
    s = Session(plan=PLAN)
    s.run(1_000, 2_000, slide=9, slide_started=0)
    assert s.state["visits"][0].get("target_ms") is None


def test_finalize_emits_the_remaining_visits_without_waiting_for_cursors():
    s = Session(plan=PLAN)
    s.run(1_000, 3_000, slide=1, slide_started=0)
    s.run(4_000, 5_000, slide=2, slide_started=3_500)  # 단어가 없어 커서가 없다
    assert slides_of(s) == []
    fin = finalize(
        {"take_id": "test-take", "t_ms": 5_000, "coach_state": s.state}, s.judges, s.config
    )
    got = [(e.slide_number, e.start_ms, e.end_ms) for e in fin.events if e.kind == "SLIDE"]
    assert got == [(1, 0, 3_500), (2, 3_500, 5_000)]


def test_pieces_still_go_to_visits_when_the_round_fails(
    session: Session, monkeypatch: pytest.MonkeyPatch
):
    session.run(1_000, 3_000)
    before = session.state["visits"][0]["tally"]["GAZE"]["total_ms"]

    def boom(*a: Any, **k: Any):
        raise RuntimeError("가짜 예외")

    monkeypatch.setattr(core_mod, "_round", boom)
    req = make_request(4_000, state=session.state)
    resp = decide_safe(req, session.judges, session.config)
    assert resp.reason_codes == ["INTERNAL_ERROR"] and resp.events == []
    after = resp.coach_state["visits"][0]["tally"]["GAZE"]["total_ms"]
    assert after > before
