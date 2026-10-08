"""판단 — 적격성 필터 · 우선순위 · WAIT / IGNORE / INTERVENE."""

from __future__ import annotations

from coach import decide
from coach.schemas import CoachingPlan, FocusItem, RelaxItem
from coach.state import dump_state, initial_state

from .conftest import Session, gaze_script, make_request, words

SPEAKING = {"relative_db": 0.0, "silence_ms": 0, "audio_live": True}


def _cand(resp, issue):
    return next(c for c in resp.candidates if c.issue_type.value == issue)


def test_waits_until_problem_persists_then_intervenes(session: Session):
    out = session.run(10_000, 13_000, gaze=gaze_script(0.9))
    assert [r.action.value for r in out] == ["WAIT", "WAIT", "WAIT", "INTERVENE"]
    assert out[0].reason_codes == ["NOT_PERSISTENT"]
    fb = out[-1].feedback
    assert fb.area.value == "GAZE" and fb.instruction.value == "LOOK_AT_CAMERA"
    assert fb.message == "대본보다 청중을 조금 더 바라보세요"
    assert fb.evidence["start_ms"] == 10_000 and fb.evidence["end_ms"] == 13_000
    assert fb.evidence["script_ratio"] == 0.9
    assert out[-1].candidate_id == "GAZE_ON_SCRIPT-10000"


def test_one_instruction_at_a_time():
    resp = decide(
        make_request(
            115_000,
            slide=3,
            slide_elapsed=35_000,
            gaze=gaze_script(0.9, streak_ms=8000),
            speech={"words": words(115_000, cpm=290)},
            state=dump_state(
                initial_state().model_copy(
                    update={
                        "slide_number": 3,
                        "slide_chars": {"3": 150},
                        "last_final_end_ms": 10**9,
                    }
                )
            ),
        )
    )
    selected = [c for c in resp.candidates if c.status.value == "SELECTED"]
    assert len(selected) == 1 and resp.feedback is not None
    assert {c.status.value for c in resp.candidates} >= {"SELECTED"}


def test_exam_mode_never_speaks_but_records(session: Session):
    out = session.run(10_000, 20_000, mode="EXAM", gaze=gaze_script(0.9))
    assert all(r.feedback is None for r in out)
    assert out[-1].action.value == "IGNORE"
    assert "EXAM_MODE" in out[-1].reason_codes
    assert any(e.kind == "SUPPRESSED" and "EXAM_MODE" in e.reasons for e in session.events)


def test_min_gap_between_any_two_messages(session: Session):
    session.run(10_000, 13_000, gaze=gaze_script(0.9))  # 13초에 시선 지적
    resp = session.step(14_000, gaze=gaze_script(0.9), speech={"words": words(14_000, cpm=420)})
    pace = _cand(resp, "PACE_FAST")
    assert "MIN_GAP" in pace.reasons


def test_cooldown_per_instruction(session: Session):
    session.run(10_000, 13_000, gaze=gaze_script(0.9))
    out = session.run(14_000, 40_000, gaze=gaze_script(0.9))
    assert all(r.feedback is None for r in out)
    assert "COOLDOWN" in _cand(out[-1], "GAZE_ON_SCRIPT").reasons


def test_low_priority_is_ignored(session: Session):
    # 기준선 겨우 넘김(심각도 0.5) × 신뢰도 0.65 → 33점 < 40
    out = session.run(10_000, 14_000, gaze=gaze_script(0.7, uncertain=0.35))
    assert out[-1].action.value == "IGNORE"
    assert out[-1].reason_codes == ["LOW_PRIORITY"]


def test_waits_for_sentence_end_then_speaks():
    s = Session()
    s.run(10_000, 12_000, gaze=gaze_script(0.9), voice=SPEAKING)
    held = s.step(13_000, gaze=gaze_script(0.9), voice=SPEAKING)
    assert held.action.value == "WAIT" and held.reason_codes == ["WAITING_FOR_PAUSE"]
    pause = s.step(
        14_000,
        gaze=gaze_script(0.9),
        voice={"relative_db": None, "silence_ms": 500, "audio_live": True},
    )
    assert pause.action.value == "INTERVENE"
    assert "PAUSE_TIMEOUT" not in pause.reason_codes


def test_utterance_end_counts_as_a_pause():
    s = Session()
    s.run(10_000, 12_000, gaze=gaze_script(0.9), voice=SPEAKING)
    resp = s.step(
        13_000, gaze=gaze_script(0.9), voice=SPEAKING, speech={"utterance_end_ms": 12_600}
    )
    assert resp.action.value == "INTERVENE"


def test_does_not_wait_forever_for_a_pause():
    s = Session()
    out = s.run(10_000, 17_000, gaze=gaze_script(0.9), voice=SPEAKING)
    actions = [r.action.value for r in out]
    assert actions.index("INTERVENE") == 6  # 13초부터 3초 기다린 16초
    assert "PAUSE_TIMEOUT" in out[6].reason_codes


def test_plan_relax_and_focus():
    relaxed = dump_state(
        initial_state(CoachingPlan(source="LLM", relax=[RelaxItem(area="GAZE", slide_number=1)]))
    )
    s = Session()
    s.state = relaxed
    out = s.run(10_000, 14_000, gaze=gaze_script(0.9))
    assert all(r.feedback is None for r in out)
    assert "PLAN_RELAXED" in _cand(out[-1], "GAZE_ON_SCRIPT").reasons

    focus = dump_state(
        initial_state(CoachingPlan(source="LLM", focus=[FocusItem(area="GAZE", weight=1.8)]))
    )
    plain = Session().run(10_000, 13_000, gaze=gaze_script(0.75))[-1]
    f = Session()
    f.state = focus
    boosted = f.run(10_000, 13_000, gaze=gaze_script(0.75))[-1]
    assert boosted.feedback.priority > plain.feedback.priority
    assert "PLAN_FOCUS" in boosted.reason_codes


def test_plan_weight_is_clamped():
    wild = dump_state(
        initial_state(CoachingPlan(source="LLM", focus=[FocusItem(area="GAZE", weight=50)]))
    )
    s = Session()
    s.state = wild
    resp = s.run(10_000, 13_000, gaze=gaze_script(0.75))[-1]
    assert resp.feedback.priority <= 100


def test_budget_from_plan():
    capped = dump_state(initial_state(CoachingPlan(max_interventions=0)))
    s = Session()
    s.state = capped
    out = s.run(10_000, 14_000, gaze=gaze_script(0.9))
    assert "BUDGET_EXHAUSTED" in _cand(out[-1], "GAZE_ON_SCRIPT").reasons


def test_mission_and_memory_raise_priority_with_reasons():
    mission = [
        {
            "mission_id": "m1",
            "area": "GAZE",
            "slide_number": 1,
            "target": {"metric": "script_ratio", "operator": "LTE", "value": 0.3},
        }
    ]
    memory = {"recurring_issues": [{"area": "GAZE", "slide_number": 1}]}
    plain = Session().run(10_000, 13_000, gaze=gaze_script(0.75))[-1]
    s = Session(missions=mission, memory=memory)
    resp = s.run(10_000, 13_000, gaze=gaze_script(0.75))[-1]
    assert resp.feedback.priority > plain.feedback.priority
    assert {"MISSION_RELEVANT", "MISSION_AT_RISK", "RECURRING"} <= set(resp.reason_codes)


def test_mission_on_other_slide_does_not_apply():
    mission = [{"mission_id": "m1", "area": "GAZE", "slide_number": 6}]
    resp = Session(missions=mission).run(10_000, 13_000, gaze=gaze_script(0.75))[-1]
    assert "MISSION_RELEVANT" not in resp.reason_codes
