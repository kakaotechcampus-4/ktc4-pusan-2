"""되돌아보기 — 개입 효과를 보고 계속 · 다른 방법 · 그만두기를 고른다."""

from __future__ import annotations

from coach.config import load_config

from .conftest import PLAN, Session, gaze_blind, gaze_off, gaze_on, mid_slide_state
from .fakes import fake_issue


def _of(session, kind):
    return [e for e in session.events if e.kind == kind]


def test_effective_feedback_earns_one_praise(session: Session):
    session.run(10_000, 13_000, **gaze_on(0.9))  # 13초 개입
    session.run(14_000, 40_000, **gaze_off(0.1))  # 고개를 듦
    outcome = _of(session, "OUTCOME")[0]
    assert outcome.outcome.value == "EFFECTIVE"
    assert (outcome.before, outcome.after) == (0.9, 0.1)
    praise = [r for r in session.interventions if r.feedback.instruction.value == "CONTINUE"]
    assert len(praise) == 1
    assert praise[0].feedback.area.value == "GAZE"
    assert praise[0].feedback.message == "좋아요, 지금처럼 이어가세요"
    assert praise[0].t_ms >= 13_000 + 15_000  # 메시지 사이 15초


def test_praise_is_dropped_if_problem_returns(session: Session):
    session.run(10_000, 13_000, **gaze_on(0.9))
    session.run(14_000, 25_000, **gaze_off(0.1))  # 25초(개입 + 12초)에 효과 있음
    session.run(26_000, 45_000, **gaze_on(0.9))  # 다시 대본만
    assert not [r for r in session.interventions if r.feedback.instruction.value == "CONTINUE"]


def test_ineffective_escalates_then_gives_up_per_slide():
    # 시간 규칙이 끼지 않게 계획 없이 시선만 본다
    s = Session(load_config(policy={"cooldown_ms": 20_000}), slide=2, plan={})
    s.run(10_000, 70_000, **gaze_on(0.9))
    msgs = [r.feedback.message for r in s.interventions]
    assert msgs == [
        "대본보다 청중을 조금 더 바라보세요",
        "문장을 시작할 때만이라도 고개를 들어 청중을 보세요",
    ]
    changes = [(e.change.value, e.to_variant) for e in _of(s, "STRATEGY")]
    assert changes == [("ESCALATED", "sentence_start"), ("GAVE_UP", None)]
    assert "STRATEGY_EXHAUSTED" in s.responses[-1].candidates[0].reasons
    assert "ESCALATED" in s.interventions[1].reason_codes

    # 3번 장에서는 처음부터 다시 시도한다
    nxt = s.run(71_000, 76_000, slide=3, slide_started=71_000, **gaze_on(0.9))
    assert any(
        r.feedback and r.feedback.message == "대본보다 청중을 조금 더 바라보세요" for r in nxt
    )


def test_outcome_not_measured_when_sensor_drops(session: Session):
    session.run(10_000, 13_000, **gaze_on(0.9))
    session.run(14_000, 26_000, **gaze_blind())  # 12초 뒤에 잰다
    outcome = _of(session, "OUTCOME")[0]
    assert outcome.outcome.value == "NOT_MEASURED"
    assert not _of(session, "STRATEGY")  # 모르는 걸로 전략을 바꾸지 않는다


def test_non_exhaustible_issue_never_gives_up():
    s = Session(load_config(policy={"cooldown_ms": 10_000, "min_gap_ms": 5_000}))
    for t in range(10_000, 80_001, 1000):
        silence = t - 4_000
        issues = [fake_issue("PAUSE", "LONG_SILENCE", 0.8)] if silence > 5_000 else []
        s.step(
            t,
            issues=issues,
            metrics={"silence_ms": silence},
            voice={"silence_ms": silence, "audio_live": True},
        )
    resumes = [r for r in s.interventions if r.feedback.instruction.value == "RESUME"]
    assert len(resumes) >= 3
    assert not [e for e in _of(s, "STRATEGY") if e.change.value == "GAVE_UP"]


def test_time_ladder_escalates_when_still_behind():
    s = Session(plan=PLAN, slide=3, slide_started=80_000)
    # 3번 장 시작이 80초. 말한 글자가 늘지 않으니 계속 늦어진다
    s.state = mid_slide_state(3, 80_000, chars=150, now_ms=114_000)
    for t in range(115_000, 141_001, 1000):
        s.step(t)
    time_fb = [r.feedback for r in s.interventions if r.feedback.area.value == "TIME"]
    assert time_fb[0].instruction.value in ("SPEED_UP", "CONDENSE")
    changes = [e for e in _of(s, "STRATEGY") if e.issue_type.value == "BEHIND_SCHEDULE"]
    assert changes and changes[0].change.value == "ESCALATED"
