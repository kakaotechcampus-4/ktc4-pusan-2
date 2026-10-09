"""측정 → 코치 규칙 — 판정 모듈이 준 결과(Detection)가 후보 · 문구 · 효과 판정으로 이어지는지.

판정 모듈의 기준(몇 CPM 부터 빠른가 …)은 모듈 테스트가 본다. 여기서는 가짜 모듈이 준 문제 · 지표를
코치가 어떻게 받아 쓰는지만 본다. 시간 판정(timing)은 코치 안의 실제 모듈이다.
"""

from __future__ import annotations

from coach import judges as judges_mod
from coach import measure
from coach.config import load_config
from coach.schemas import CoachRequest
from coach.state import initial_state

from .conftest import PLAN, Session, gaze_blind, gaze_on, make_request, mid_slide_state
from .fakes import fake_issue, fake_judges

PACE = fake_issue("SPEED", "PACE_FAST", 0.8)


def _cand(resp, issue):
    return next((c for c in resp.candidates if c.issue_type.value == issue), None)


def test_persistence_counts_only_reliable_time():
    """센서가 나쁜 동안은 지속시간을 세지 않는다 — 좋아진 뒤 3초를 다시 채워야 말한다."""
    s = Session()
    s.run(10_000, 20_000, **gaze_blind())
    out = s.run(21_000, 45_000, **gaze_on(0.9))
    first = next(r.t_ms for r in out if r.feedback is not None)
    assert first >= 21_000 + 3_000


def test_unmeasurable_result_makes_the_candidate_unusable():
    s = Session()
    resp = s.step(10_000, **gaze_blind())
    c = _cand(resp, "GAZE_ON_SCRIPT")
    assert c.status.value == "IGNORED" and "SENSOR_UNUSABLE" in c.reasons


def test_not_actionable_issue_is_unusable_even_if_measurable():
    s = Session()
    issue = fake_issue("SPEED", "PACE_FAST", 0.8, actionable=False)
    c = _cand(s.step(10_000, issues=[issue]), "PACE_FAST")
    assert c.status.value == "IGNORED" and "SENSOR_UNUSABLE" in c.reasons


# ── 시간: 코치 안의 timing 모듈 결과가 말로 이어진다 ────────────────────────────


def _behind(**kw):
    """115초, 3번 장(80초 시작)의 1/4(150/600자): 남은 내용 75초 ÷ 남은 시간 65초 → r 1.15."""
    s = Session(plan=PLAN, slide=3, slide_started=80_000)
    s.state = mid_slide_state(3, 80_000, chars=150, now_ms=114_000)
    return s, s.step(115_000, **kw)


def test_behind_but_catchable_says_speed_up():
    # 300 × 1.15 = 346 ≤ 350 → 속도로 따라잡을 수 있다
    _, resp = _behind()
    assert resp.indicators["TIME"] == "BEHIND"
    assert _cand(resp, "BEHIND_SCHEDULE").instruction.value == "SPEED_UP"
    assert resp.action.value == "INTERVENE"
    assert resp.feedback.message == "조금만 빠르게 — 남은 2장, 1분 5초"


def test_behind_beyond_the_speed_limit_says_condense():
    # 370 × 1.15 = 427 > 350 → 속도로는 못 따라잡는다 → 내용을 줄인다
    _, resp = _behind(metrics={"cpm": 370.0})
    assert _cand(resp, "BEHIND_SCHEDULE").instruction.value == "CONDENSE"


def test_slow_down_is_suppressed_while_behind():
    _, resp = _behind(issues=[PACE])
    assert "TIME_PRESSURE" in _cand(resp, "PACE_FAST").reasons


def test_on_plan_final_minute_says_nothing():
    # 계획상 2분 10초에 3번 장 끝무렵이면 정상 — '결론으로'를 말하지 않는다
    s = Session(plan=PLAN, slide=3, slide_started=80_000)
    s.state = mid_slide_state(3, 80_000, chars=450, now_ms=129_000)
    resp = s.step(130_000)
    assert _cand(resp, "FINAL_MINUTE") is None
    assert resp.indicators["TIME"] == "ON_TRACK"


def test_final_minute_without_slide_plan_falls_back_to_remaining_time():
    s = Session(plan={"target_ms": 180_000}, slide=None)
    resp = s.step(125_000)
    assert _cand(resp, "FINAL_MINUTE").instruction.value == "WRAP_UP"


def test_time_over_once():
    s = Session(plan={"target_ms": 60_000}, slide=None)
    s.run(61_000, 200_000)
    wraps = [r for r in s.interventions if r.feedback.instruction.value == "WRAP_UP"]
    assert len(wraps) == 1
    assert wraps[0].feedback.message == "제한 시간을 넘겼어요 — 한 문장으로 마무리하세요"


def test_slide_over_says_move_on_but_not_on_last_slide():
    s = Session(plan=PLAN, slide=2, slide_started=5_000)
    s.state = mid_slide_state(2, 5_000, chars=500, now_ms=99_000)
    c = _cand(s.step(100_000), "SLIDE_OVER")
    assert c.instruction.value == "MOVE_ON"
    last = Session(plan=PLAN, slide=4, slide_started=110_000)
    last.state = mid_slide_state(4, 110_000, chars=200, now_ms=169_000)
    assert _cand(last.step(170_000), "SLIDE_OVER") is None


# ── 효과 판정 ────────────────────────────────────────────────────────────


def test_silence_outcome_sees_speech_even_without_speaking_samples():
    cfg = load_config(policy={"pause_wait_max_ms": 0})
    s = Session(cfg)
    for t in range(10_000, 17_001, 1_000):
        silence = t - 10_000
        issues = [fake_issue("PAUSE", "LONG_SILENCE", 0.8)] if silence > 5_000 else []
        s.step(
            t,
            issues=issues,
            metrics={"silence_ms": silence},
            voice={"silence_ms": silence, "audio_live": True},
        )
    assert any(r.feedback and r.feedback.instruction.value == "RESUME" for r in s.responses)
    # 다시 말했지만 매 틱 침묵이 300ms 이상으로 잡힌다 (단어 사이 틈) — 그래도 침묵이 다시 시작됐다
    for t in range(18_000, 25_001, 1_000):
        s.step(t, metrics={"silence_ms": 600}, voice={"silence_ms": 600, "audio_live": True})
    outcome = next(e for e in s.events if e.kind == "OUTCOME")
    assert outcome.outcome.value == "EFFECTIVE"


def test_stt_issues_are_unusable_while_audio_is_dead():
    """STT 상태가 ok 여도 오디오가 멈췄으면 말 속도 · 군더더기로 말을 걸지 않는다."""
    s = Session()
    dead = {"silence_ms": 400, "audio_live": False}
    issue = fake_issue("FILLER", "FILLER_FREQUENT", 0.9)
    c = _cand(s.step(10_000, issues=[issue], voice=dead), "FILLER_FREQUENT")
    assert c.status.value == "IGNORED" and "SENSOR_UNUSABLE" in c.reasons


def test_slide_mission_current_value_is_the_slide_dwell():
    """장 미션(slide_duration_ms)의 지금 값은 그 장에 머문 시간이다."""
    req = CoachRequest.model_validate(
        make_request(40_000, plan=PLAN, slide=2, slide_started=30_000)
    )
    state = initial_state()
    run = judges_mod.run(req, state, fake_judges(), load_config())
    tick = measure.build_tick(req, load_config(), state, run)
    assert tick.metrics["slide_duration_ms"] == tick.metrics["slide_elapsed_ms"] == 10_000


def test_time_critical_reason_with_float_remaining():
    s = Session(plan=PLAN)
    out = s.run(170_000, 172_000)
    reasons = {r for resp in out for c in resp.candidates for r in c.reasons}
    feedback = [r for r in out if r.feedback is not None]
    assert "TIME_CRITICAL" in reasons or any("TIME_CRITICAL" in r.reason_codes for r in feedback)


def test_stt_metrics_are_dropped_while_audio_is_dead():
    """오디오가 멈추면 말 속도 · 군더더기 지표와 새 군더더기 수를 규칙에 넘기지 않는다."""
    dead = {"silence_ms": 400, "audio_live": False}
    req = CoachRequest.model_validate(make_request(10_000, plan=PLAN, voice=dead))
    state = initial_state()
    run = judges_mod.run(req, state, fake_judges(), load_config())
    tick = measure.build_tick(req, load_config(), state, run)
    assert tick.stt_ok is False
    assert tick.metrics.get("cpm") is None and tick.filler_new == 0
