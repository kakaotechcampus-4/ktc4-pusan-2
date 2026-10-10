"""시선 문제 셋의 공유 사다리, 기록만 하는 문제, 참은 기록의 단위."""

from __future__ import annotations

from typing import Any

from coach.config import load_config
from coach.state import episode_key, strategy_key
from coach.vocab import SHARED_LADDER, Issue

from .conftest import Session, gaze_on
from .fakes import fake_issue

FIRST = "대본보다 청중을 조금 더 바라보세요"
SECOND = "문장을 시작할 때만이라도 고개를 들어 청중을 보세요"


def gaze_issue(issue_type: str, severity: float = 0.8) -> dict[str, Any]:
    return fake_issue("GAZE", issue_type, severity)


def kinds(session: Session, kind: str) -> list[Any]:
    return [e for e in session.events if e.kind == kind]


def session(**policy: int) -> Session:
    # 시간 규칙이 끼지 않게 계획 없이 시선만 본다
    return Session(load_config(policy={"cooldown_ms": 20_000, **policy}), slide=1, plan={})


def test_keys_split_episode_and_ladder():
    # 구간은 문제마다, 사다리는 장마다 하나
    assert episode_key(Issue.GAZE_AWAY, 1) == "GAZE_AWAY:1"
    assert strategy_key(Issue.GAZE_AWAY, 1) == "GAZE:1"
    assert strategy_key(Issue.GAZE_ON_SCRIPT, 2) == "GAZE:2"
    assert strategy_key(Issue.GAZE_LOW_EYE_CONTACT, None) == "GAZE"
    assert episode_key(Issue.PACE_FAST, 1) == strategy_key(Issue.PACE_FAST, 1) == "PACE_FAST"
    assert episode_key(Issue.SLIDE_OVER, 3) == strategy_key(Issue.SLIDE_OVER, 3) == "SLIDE_OVER:3"
    assert set(SHARED_LADDER) == {
        Issue.GAZE_ON_SCRIPT,
        Issue.GAZE_AWAY,
        Issue.GAZE_LOW_EYE_CONTACT,
    }


def test_gaze_away_continues_the_ladder_of_the_same_slide():
    s = session()
    s.run(10_000, 26_000, **gaze_on(0.9))  # 13초에 첫 말, 25초에 효과 없음 → 다음 칸
    assert [r.feedback.message for r in s.interventions] == [FIRST]
    assert [(e.change.value, e.to_variant) for e in kinds(s, "STRATEGY")] == [
        ("ESCALATED", "sentence_start")
    ]

    # 다른 문제가 걸려도 두 번째 칸에서 이어진다 (처음 칸을 되풀이하지 않는다)
    s.run(27_000, 35_000, issues=[gaze_issue("GAZE_AWAY")])
    assert [r.feedback.message for r in s.interventions] == [FIRST, SECOND]
    assert "ESCALATED" in s.interventions[1].reason_codes
    assert s.state["strategy"]["GAZE:1"]["step"] == 1


def test_last_step_failure_exhausts_the_ladder_for_the_other_gaze_issues():
    s = session()
    s.run(10_000, 70_000, **gaze_on(0.9))
    assert [e.change.value for e in kinds(s, "STRATEGY")] == ["ESCALATED", "GAVE_UP"]

    # 같은 장의 다른 시선 문제는 말하지 않는다
    out = s.run(71_000, 76_000, issues=[gaze_issue("GAZE_LOW_EYE_CONTACT")])
    assert all(r.feedback is None for r in out)
    view = s.cand(out[-1], "GAZE_LOW_EYE_CONTACT")
    assert view.status.value == "IGNORED" and "STRATEGY_EXHAUSTED" in view.reasons

    # 다음 장은 처음부터
    nxt = s.run(
        77_000,
        81_000,
        slide=2,
        slide_started=77_000,
        issues=[gaze_issue("GAZE_LOW_EYE_CONTACT")],
    )
    assert [r.feedback.message for r in nxt if r.feedback] == [FIRST]
    assert s.state["strategy"]["GAZE:2"]["fires"] == 1


def test_two_gaze_issues_make_two_episodes_but_one_ladder():
    s = session()
    both = [gaze_issue("GAZE_ON_SCRIPT"), gaze_issue("GAZE_AWAY")]
    s.run(10_000, 20_000, issues=both)
    # 말은 사다리 하나에서 한 번만 나온다
    assert len(s.interventions) == 1
    assert set(s.state["strategy"]) == {"GAZE:1"}
    assert s.state["strategy"]["GAZE:1"]["fires"] == 1
    assert set(s.state["episodes"]) == {"GAZE_ON_SCRIPT:1", "GAZE_AWAY:1"}

    s.run(21_000, 26_000)  # 문제가 사라지면 구간이 닫힌다
    episodes = kinds(s, "EPISODE")
    assert sorted(e.issue_type.value for e in episodes) == ["GAZE_AWAY", "GAZE_ON_SCRIPT"]
    assert len({e.event_id for e in episodes}) == 2
    assert {e.event_id for e in episodes} == {"ep-GAZE_AWAY-1-10000", "ep-GAZE_ON_SCRIPT-1-10000"}
    # 개입은 먼저 고른 후보의 구간에만 이어진다
    said = [e for e in episodes if e.intervention_ids]
    assert len(said) == 1


def test_record_only_issues_are_episodes_but_never_candidates():
    s = session()
    unmeasurable = fake_issue("GAZE", "GAZE_UNMEASURABLE", 0.8, actionable=False)
    record_only = [
        gaze_issue("GAZE_ON_SCREEN"),
        unmeasurable,
        fake_issue("SPEED", "PACE_SLOW", 0.8),
    ]
    out = s.run(10_000, 30_000, issues=record_only)
    s.run(31_000, 36_000)

    assert all(r.feedback is None for r in out)
    for resp in out:
        assert s.candidates_of(resp) == []
    assert not kinds(s, "SUPPRESSED") and not kinds(s, "INTERVENTION")
    assert {e.issue_type.value for e in kinds(s, "EPISODE")} == {
        "GAZE_ON_SCREEN",
        "GAZE_UNMEASURABLE",
        "PACE_SLOW",
    }
    assert {e.event_id for e in kinds(s, "EPISODE")} == {
        "ep-GAZE_ON_SCREEN-1-10000",
        "ep-GAZE_UNMEASURABLE-1-10000",
        "ep-PACE_SLOW-1-10000",
    }


def test_record_only_issue_does_not_hide_a_coachable_one():
    s = session()
    out = s.run(10_000, 13_000, issues=[gaze_issue("GAZE_ON_SCREEN"), gaze_issue("GAZE_AWAY")])
    assert out[-1].feedback is not None
    assert [c.issue_type.value for c in s.candidates_of(out[-1])] == ["GAZE_AWAY"]


def test_suppressed_gap_is_per_issue():
    s = session()
    # 실전 모드라 말하지 않고 참은 기록만 남는다. 둘째 문제는 3초 늦게 시작한다
    for t in range(10_000, 24_001, 1000):
        issues = [gaze_issue("GAZE_ON_SCRIPT")]
        if t >= 13_000:
            issues.append(gaze_issue("GAZE_AWAY"))
        s.step(t, issues=issues, mode="EXAM")
    logged = [(e.issue_type.value, e.t_ms) for e in kinds(s, "SUPPRESSED")]
    # 첫 문제가 10초에 남긴 기록이 둘째 문제의 기록을 막지 않고, 각자 10초 간격이다
    assert logged == [
        ("GAZE_ON_SCRIPT", 10_000),
        ("GAZE_AWAY", 13_000),
        ("GAZE_ON_SCRIPT", 20_000),
        ("GAZE_AWAY", 23_000),
    ]
    assert {"GAZE_ON_SCRIPT:1", "GAZE_AWAY:1"} <= set(s.state["suppress_log"])
