"""이벤트 id — 종류와 대상으로 만든다. 같은 입력이면 같은 id, 다른 이벤트는 겹치지 않는다."""

from __future__ import annotations

from typing import Any

from coach import finalize
from coach.config import load_config
from coach.events import EventSink
from coach.schemas import StrategyEvent, SuppressedEvent
from coach.state import initial_state

from .conftest import Session, gaze_off, gaze_on


def _slide(e: Any) -> str:
    return "none" if e.slide_number is None else str(e.slide_number)


def _expected_id(e: Any) -> str:
    match e.kind:
        case "INTERVENTION":
            return f"iv-{e.t_ms}"
        case "OUTCOME":
            return f"oc-{e.intervention_id}"
        case "EPISODE":
            return f"ep-{e.issue_type.value}-{_slide(e)}-{e.start_ms}"
        case "SLIDE":
            return f"sl-{_slide(e)}-{e.start_ms}"
        case "STRATEGY":
            return f"st-{e.issue_type.value}-{_slide(e)}-{e.t_ms}"
        case "SUPPRESSED":
            return f"su-{e.issue_type.value}-{e.t_ms}"
    raise AssertionError(e.kind)


def _take() -> tuple[Session, list[Any]]:
    """효과 없는 개입(사다리 오르기 · 그만두기), 참은 기록, 문제 구간, 장 전환을 모두 만든다."""
    s = Session(load_config(policy={"cooldown_ms": 20_000}), slide=2, plan={})
    s.run(10_000, 70_000, **gaze_on(0.9))
    s.run(71_000, 80_000, slide=3, slide_started=71_000, **gaze_off(0.1))
    fin = finalize(
        {"take_id": "test-take", "t_ms": 81_000, "coach_state": s.state}, s.judges, s.config
    )
    return s, [*s.events, *fin.events]


def test_event_ids_follow_kind_and_target():
    _, events = _take()
    assert {e.kind for e in events} == {
        "INTERVENTION",
        "OUTCOME",
        "EPISODE",
        "SLIDE",
        "STRATEGY",
        "SUPPRESSED",
    }
    for e in events:
        assert e.event_id == _expected_id(e), e


def test_intervention_id_is_the_event_id():
    _, events = _take()
    ivs = [e for e in events if e.kind == "INTERVENTION"]
    assert ivs and all(e.intervention_id == e.event_id for e in ivs)
    assert ivs[0].event_id == "iv-13000"
    # 효과와 사다리 이벤트는 개입의 id 를 가리킨다
    ids = {e.intervention_id for e in ivs}
    refs = [e for e in events if e.kind in ("OUTCOME", "STRATEGY")]
    assert refs and all(e.intervention_id in ids for e in refs)


def test_event_ids_are_unique_within_a_take():
    _, events = _take()
    ids = [e.event_id for e in events]
    assert len(set(ids)) == len(ids)


def test_same_requests_give_the_same_event_ids():
    _, first = _take()
    _, second = _take()
    assert [e.event_id for e in first] == [e.event_id for e in second]


def test_same_shaped_ids_in_one_response_get_a_suffix():
    """요청이 오래 빠져 같은 문제의 효과 둘을 한 번에 재면 사다리 이벤트가 같은 시각에 둘 나온다."""
    sink = EventSink(initial_state())
    strategy = {
        "t_ms": 61_000,
        "issue_type": "FILLER_FREQUENT",
        "area": "FILLER",
        "slide_number": 2,
        "change": "ESCALATED",
        "from_instruction": "REDUCE_FILLER",
        "from_variant": "default",
        "failures": 1,
    }
    a = sink.emit(StrategyEvent, **strategy, intervention_id="iv-10000")
    b = sink.emit(StrategyEvent, **strategy, intervention_id="iv-30000")
    suppressed = {
        "t_ms": 38_000,
        "candidate_id": "c",
        "issue_type": "IMPROVED_AFTER_FEEDBACK",
        "instruction": "CONTINUE",
        "status": "WAITING",
        "priority": 30,
        "reasons": ["MIN_GAP"],
    }
    c = sink.emit(SuppressedEvent, **suppressed, area="GAZE")
    d = sink.emit(SuppressedEvent, **suppressed, area="SPEED")
    assert [a.event_id, b.event_id] == [
        "st-FILLER_FREQUENT-2-61000",
        "st-FILLER_FREQUENT-2-61000-2",
    ]
    assert [c.event_id, d.event_id] == [
        "su-IMPROVED_AFTER_FEEDBACK-38000",
        "su-IMPROVED_AFTER_FEEDBACK-38000-2",
    ]
