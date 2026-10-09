"""개입 규칙 — 효과 판정 표 · 적격성 · 우선순위 가중 (#150 5절).

판정 모듈은 가짜다. 규칙이 표대로 값을 읽는지만 본다.
"""

from __future__ import annotations

from typing import Any

import pytest

from coach import core as core_mod
from coach import decide_safe, measure, priority, reflection
from coach import judges as judges_mod
from coach.candidates import Candidate
from coach.config import load_config
from coach.schemas import CoachRequest, IssueCriteria, JudgmentResult, Mission
from coach.state import CoachState, Cursor, HistorySample, PendingOutcome, dump_state, initial_state
from coach.tick import Tick
from coach.timing import criteria as timing_criteria
from coach.vocab import ISSUE_TYPE, Instruction, Issue, Outcome

from .conftest import PLAN, Session, gaze_on, make_request
from .fakes import FakeJudge, ScriptedJudge, _criteria, fake_issue, fake_judges

CFG = load_config()
BEHIND = timing_criteria(CFG.timing)["BEHIND_SCHEDULE"].threshold


def _tick(
    t_ms: int = 60_000,
    *,
    metrics: dict[str, Any] | None = None,
    state: CoachState | None = None,
    criteria: dict[str, dict[str, Any]] | None = None,
    stt_ok: bool = True,
    slide: int | None = 1,
    speaking: bool | None = None,
    **request: Any,
) -> Tick:
    req = CoachRequest.model_validate(make_request(t_ms, **request))
    return Tick(
        req=req,
        cfg=CFG,
        state=state or initial_state(),
        t=t_ms,
        slide_number=slide,
        slide_start_ms=0,
        stt_ok=stt_ok,
        metrics=metrics or {},
        speaking=speaking,
        criteria={
            module: {k: IssueCriteria.model_validate(v) for k, v in found.items()}
            for module, found in (criteria or {}).items()
        },
    )


#: 판정 모듈이 공개한 기준: PACE_FAST 350 · VOLUME_LOW -6 (가짜 모듈과 같다), 청중 응시 0.3
CRITERIA = {
    "pace": {"PACE_FAST": _criteria("cpm", 350.0, 450.0)},
    "volume": {"VOLUME_LOW": _criteria("voice_diff_db", -6.0, -15.0, lower=True)},
    "gaze": {"GAZE_LOW_EYE_CONTACT": _criteria("audience_ratio", 0.3, 0.1, lower=True)},
    "timing": {"BEHIND_SCHEDULE": _criteria("required_ratio", BEHIND, 1.0)},
}


def _pending(
    issue: Issue,
    *,
    before: float | None,
    t_ms: int = 40_000,
    delay: int = 10_000,
    slide: int | None = 1,
) -> PendingOutcome:
    return PendingOutcome(
        intervention_id="iv-1",
        issue_type=issue,
        area=ISSUE_TYPE[issue],
        step=0,
        strategy_key=issue.value,
        slide_number=slide,
        t_ms=t_ms,
        check_at_ms=t_ms + delay,
        metric=reflection.OUTCOME_METRIC[issue],
        before=before,
    )


def _judge(
    issue: Issue, before: float | None, after: float | None, **tick: Any
) -> tuple[Outcome, float | None, float | None]:
    metric = reflection.OUTCOME_METRIC[issue]
    t = _tick(metrics={metric: after}, criteria=CRITERIA, **tick)
    m = reflection.judge(t, _pending(issue, before=before))
    return m.outcome, m.before, m.after


EFF, INEFF, NM = Outcome.EFFECTIVE, Outcome.INEFFECTIVE, Outcome.NOT_MEASURED


# ── A. 효과 판정 표 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("issue", "before", "after", "expected"),
    [
        # 회복 기준(0.6) 아래로 내려와야 한다. 줄기만 해서는 안 된다
        (Issue.GAZE_ON_SCRIPT, 0.9, 0.59, EFF),
        (Issue.GAZE_ON_SCRIPT, 0.95, 0.65, INEFF),
        (Issue.GAZE_AWAY, 0.5, 0.19, EFF),
        (Issue.GAZE_AWAY, 0.5, 0.2, INEFF),
        # 청중 응시는 기준(0.3)에 여유폭(0.1)을 더한 값 위로 올라와야 한다
        (Issue.GAZE_LOW_EYE_CONTACT, 0.1, 0.4, EFF),
        (Issue.GAZE_LOW_EYE_CONTACT, 0.1, 0.39, INEFF),
        # 말 속도: 기준 아래로 내려와야 한다 — 10% 줄었다고 효과가 되지 않는다
        (Issue.PACE_FAST, 400, 349, EFF),
        (Issue.PACE_FAST, 400, 350, INEFF),
        (Issue.PACE_FAST, 450, 380, INEFF),
        # 음량: 기준 위로 올라와야 한다 — 3dB 커졌다고 효과가 되지 않는다
        (Issue.VOLUME_LOW, -9, -6, EFF),
        (Issue.VOLUME_LOW, -15, -9, INEFF),
        # 늦음: 기준 아래로, 또는 정해진 폭 이상 줄어듦
        (Issue.BEHIND_SCHEDULE, 1.5, BEHIND - 0.01, EFF),
        (Issue.BEHIND_SCHEDULE, 1.5, 1.4, EFF),
        (Issue.BEHIND_SCHEDULE, 1.5, 1.48, INEFF),
        # 일찍 끝남: 말 속도가 정해진 비율 이상 느려져야 한다
        (Issue.AHEAD_OF_SCHEDULE, 400, 360, EFF),
        (Issue.AHEAD_OF_SCHEDULE, 400, 370, INEFF),
    ],
)
def test_outcome_table(issue: Issue, before: float, after: float, expected: Outcome):
    outcome, got_before, got_after = _judge(issue, before, after)
    assert outcome == expected
    assert (got_before, got_after) == (before, after)


@pytest.mark.parametrize(
    "issue",
    [
        Issue.GAZE_ON_SCRIPT,
        Issue.GAZE_AWAY,
        Issue.GAZE_LOW_EYE_CONTACT,
        Issue.PACE_FAST,
        Issue.VOLUME_LOW,
        Issue.BEHIND_SCHEDULE,
        Issue.AHEAD_OF_SCHEDULE,
    ],
)
def test_outcome_not_measured_without_the_metric(issue: Issue):
    assert _judge(issue, 0.5, None)[0] == NM


@pytest.mark.parametrize(
    "issue",
    [Issue.GAZE_LOW_EYE_CONTACT, Issue.PACE_FAST, Issue.VOLUME_LOW, Issue.BEHIND_SCHEDULE],
)
def test_outcome_not_measured_without_the_criterion(issue: Issue):
    metric = reflection.OUTCOME_METRIC[issue]
    tick = _tick(metrics={metric: 0.5}, criteria={})
    assert reflection.judge(tick, _pending(issue, before=0.5)).outcome == NM


def test_outcome_uses_short_average_of_the_metric():
    """효과 전후는 최근 평균이다: 한 번 튄 값이 판정을 정하지 않는다."""
    history = [HistorySample(t_ms=t, script_ratio_short=0.9) for t in (58_000, 59_000)]
    state = CoachState(history=history)
    tick = _tick(metrics={"script_ratio_short": 0.3}, state=state)  # 평균 (0.9+0.9+0.3)/3 = 0.7
    assert reflection.judge(tick, _pending(Issue.GAZE_ON_SCRIPT, before=0.9)).outcome == INEFF


def test_outcome_slide_over_and_silence():
    moved = reflection.judge(_tick(slide=2), _pending(Issue.SLIDE_OVER, before=None, slide=1))
    stayed = reflection.judge(_tick(slide=1), _pending(Issue.SLIDE_OVER, before=None, slide=1))
    assert (moved.outcome, stayed.outcome) == (EFF, INEFF)

    # 개입 t=40초, 지금 t=60초
    def silence(ms: int | None, speaking: bool | None = None) -> Outcome:
        tick = _tick(metrics={"silence_ms": ms}, speaking=speaking)
        return reflection.judge(tick, _pending(Issue.LONG_SILENCE, before=None)).outcome

    assert silence(5_000) == EFF  # 지금 침묵이 개입 뒤에 시작됐다
    assert silence(30_000) == INEFF  # 개입 전부터 이어진 침묵
    assert silence(30_000, speaking=True) == EFF
    assert silence(None) == NM


# ── FILLER_FREQUENT: 말한 시각으로 구간을 센다 ────────────────────────────────────

L = 30_000  # 군더더기 효과를 재는 구간 길이 (outcome_delay_ms)


class TalliedFiller(ScriptedJudge):
    """군더더기 모듈: 단어가 확정되는 틱(finalized_at)에 말한 시각(start)의 몫을 낸다."""

    def __init__(
        self,
        issue_at: int,
        finalized_at: dict[int, list[int]],
        *,
        raise_at: set[int] | None = None,
        unmeasurable_at: tuple[int, ...] = (),
    ) -> None:
        issue = fake_issue("FILLER", "FILLER_FREQUENT", 0.9)

        def spec(t: int) -> dict[str, Any]:
            return {
                "issues": [issue] if t == issue_at else [],
                "unmeasurable": {"FILLER"} if t in unmeasurable_at else set(),
            }

        super().__init__("filler", spec, raise_at=raise_at)
        self.finalized_at = finalized_at

    def judge(self, inputs: dict[str, Any], t_ms: int) -> list[dict[str, Any]]:
        results = super().judge(inputs, t_ms)
        for start in self.finalized_at.get(t_ms, []):
            results[0]["tally"].append({"t_ms": start, "values": {"filler_count": 1}})
        return results


def _filler_take(
    finalized_at: dict[int, list[int]],
    *,
    t0: int = 40_000,
    gap_at: tuple[int, ...] = (),
    **filler: Any,
) -> Session:
    """t0 에 군더더기 지적을 하고 t0 + L 에 효과를 잰다. gap_at 의 틱에서는 STT 가 불량이다."""
    s = Session(judges=fake_judges(filler=TalliedFiller(t0, finalized_at, **filler)))
    for t in range(1_000, t0 + L + 2_000, 1_000):
        s.step(t, stt_status="error" if t in gap_at else "ok")
    said = [
        e.t_ms
        for e in s.events
        if e.kind == "INTERVENTION" and e.issue_type.value == "FILLER_FREQUENT"
    ]
    assert said == [t0]
    return s


def _filler_outcome(s: Session):
    return next(e for e in s.events if e.kind == "OUTCOME")


def test_filler_outcome_counts_windows_by_the_spoken_time():
    # 개입 t0=40초. 앞 구간 (10, 40], 뒤 구간 (40, 70]. 39.5초에 한 말은 개입 뒤인 42초에야
    # 확정된다 — 개입 순간에 세면 앞 구간에서 빠진다
    s = _filler_take({20_000: [15_000], 42_000: [39_500], 50_000: [45_000], 55_000: [9_000]})
    e = _filler_outcome(s)
    assert e.metric == "filler_count"
    assert (e.before, e.after) == (2, 1)  # 9초에 한 말(구간 밖)은 세지 않는다
    assert e.outcome.value == "EFFECTIVE"  # 1 <= 2 * (1 - 0.5)


def test_filler_outcome_ineffective_when_not_halved():
    s = _filler_take({20_000: [15_000], 42_000: [39_500], 50_000: [45_000, 46_000]})
    e = _filler_outcome(s)
    assert (e.before, e.after, e.outcome.value) == (2, 2, "INEFFECTIVE")


def test_filler_outcome_not_measured_after_an_stt_gap_inside_the_window():
    s = _filler_take({20_000: [15_000]}, gap_at=(25_000,))  # 앞 구간 안의 끊김
    e = _filler_outcome(s)
    assert (e.outcome.value, e.before, e.after) == ("NOT_MEASURED", None, None)
    assert not [x for x in s.events if x.kind == "STRATEGY"]


def test_filler_outcome_not_measured_while_stt_is_down_at_check_time():
    s = _filler_take({20_000: [15_000]}, gap_at=(70_000,))
    assert _filler_outcome(s).outcome.value == "NOT_MEASURED"


def test_filler_outcome_not_measured_after_a_filler_exception_inside_the_window():
    s = _filler_take({20_000: [15_000]}, raise_at={50_000})  # 뒤 구간 안에서 모듈 예외
    assert _filler_outcome(s).outcome.value == "NOT_MEASURED"


def test_filler_outcome_not_measured_when_filler_was_unmeasurable_inside_the_window():
    s = _filler_take({20_000: [15_000]}, unmeasurable_at=(30_000,))
    assert _filler_outcome(s).outcome.value == "NOT_MEASURED"


def test_filler_outcome_not_measured_after_an_internal_error_inside_the_window(
    monkeypatch: pytest.MonkeyPatch,
):
    """코치 예외로 건너뛴 시간도 군더더기 수를 믿을 수 없던 시간이다."""
    judges = fake_judges(filler=TalliedFiller(40_000, {20_000: [15_000]}))
    s = Session(judges=judges)
    for t in range(1_000, 40_000 + L + 2_000, 1_000):
        if t != 55_000:
            s.step(t)
            continue
        with monkeypatch.context() as m:
            m.setattr(core_mod.measure, "build_tick", lambda *a: 1 / 0)
            resp = decide_safe(make_request(t, state=s.state), judges, s.config)
        assert resp.reason_codes == ["INTERNAL_ERROR"]
        s.state = resp.coach_state
    assert _filler_outcome(s).outcome.value == "NOT_MEASURED"


def test_filler_outcome_not_measured_when_stt_came_back_inside_the_window():
    # 25초 불량 → 26초 복구: 25~26초는 아직 믿을 수 없던 시간이다. 개입 55초의 앞 구간 (25, 55]
    s = _filler_take({40_000: [35_000]}, t0=55_000, gap_at=(25_000,))
    assert _filler_outcome(s).outcome.value == "NOT_MEASURED"


def test_trusted_filler_time_is_merged_in_any_piece_order():
    state = CoachState(stt_ok_since_ms=None)
    pieces = [
        {"t_ms": 1_000, "values": {"total_ms": 1_000}},
        {"t_ms": 0, "values": {"total_ms": 1_000}},
    ]
    result = JudgmentResult.model_validate(
        {
            "evaluator": "filler",
            "area": "FILLER",
            "t_ms": 2_000,
            "counted_until_ms": 2_000,
            "criteria_version": "filler-fake-1",
            "measurable": True,
            "state": "NORMAL",
            "tally": pieces,
        }
    )
    measure._note_filler_ok(state, result)
    assert state.filler_ok == [[0, 2_000]]


def test_filler_outcome_measured_when_the_gap_was_before_the_window():
    # 개입 t0=80초: 앞 구간 (50, 80]. 5초의 끊김은 구간 밖이다
    s = _filler_take({60_000: [55_000, 56_000]}, t0=80_000, gap_at=(5_000,))
    assert _filler_outcome(s).outcome.value in ("EFFECTIVE", "INEFFECTIVE")
    assert _filler_outcome(s).before == 2


def test_filler_times_are_pruned_to_what_the_outcome_needs():
    s = _filler_take({t + 1_000: [t] for t in range(2_000, 70_000, 2_000)})
    kept = [at for at, _ in CoachState.model_validate(s.state).filler_times]
    last = 40_000 + L + 1_000  # 효과를 잰 뒤라 재야 할 개입이 없다 — 최근 한 구간만 남는다
    assert kept and min(kept) > last - L


# ── B. 적격성 ──────────────────────────────────────────────────────────────────


def _reasons(s: Session, resp, issue: str = "GAZE_ON_SCRIPT") -> list[str]:
    return s.cand(resp, issue).reasons


def test_script_allowed_ignores_only_script_gaze():
    s = Session(script_used=True)
    resp = s.run(10_000, 14_000, **gaze_on(0.9))[-1]
    c = s.cand(resp, "GAZE_ON_SCRIPT")
    assert c.status.value == "IGNORED" and "SCRIPT_ALLOWED" in c.reasons
    assert resp.feedback is None

    away = fake_issue("GAZE", "GAZE_AWAY", 0.8)
    s2 = Session(script_used=True)
    out = s2.run(10_000, 14_000, issues=[away])
    assert "SCRIPT_ALLOWED" not in s2.cand(out[-1], "GAZE_AWAY").reasons
    assert any(r.feedback for r in out)


@pytest.mark.parametrize("used", [False, None])
def test_script_allowed_needs_script_used(used):
    s = Session(script_used=used)
    resp = s.run(10_000, 14_000, **gaze_on(0.9))[-1]
    assert "SCRIPT_ALLOWED" not in _reasons(s, resp)


def _mission(area: str, slide: int | None) -> dict[str, Any]:
    return {"mission_id": "m", "area": area, "slide_number": slide}


@pytest.mark.parametrize(
    ("relax_slide", "mission", "relaxed"),
    [
        (1, None, True),  # 미션이 없으면 그대로
        (1, _mission("GAZE", None), False),  # Take 전체 미션은 모든 장과 겹친다
        (1, _mission("GAZE", 1), False),  # 그 장의 미션
        (1, _mission("GAZE", 2), True),  # 다른 장의 미션
        (1, _mission("SPEED", None), True),  # 다른 영역의 미션
        (None, _mission("GAZE", 5), False),  # Take 전체 봐주기는 어느 장의 미션과도 겹친다
        (None, _mission("SPEED", 1), True),
    ],
)
def test_plan_relax_is_ignored_when_it_overlaps_a_mission(relax_slide, mission, relaxed):
    relax = {"area": "GAZE"} if relax_slide is None else {"area": "GAZE", "slide_number": 1}
    s = Session(coaching_plan={"relax": [relax]}, missions=[mission] if mission else [])
    out = s.run(10_000, 14_000, **gaze_on(0.9))
    assert ("PLAN_RELAXED" in _reasons(s, out[-1])) is relaxed
    assert any(r.feedback for r in out) is not relaxed


def test_wrap_up_is_exempt_from_the_budget_but_counts():
    plan = {"max_interventions": 0}
    s = Session(plan=PLAN, coaching_plan=plan)
    resp = s.step(170_000, **gaze_on(0.9))
    assert "BUDGET_EXHAUSTED" in _reasons(s, resp)  # 시선 지적은 상한에 걸린다
    wrap = s.cand(resp, "FINAL_MINUTE")
    assert wrap is not None and "BUDGET_EXHAUSTED" not in wrap.reasons
    assert resp.feedback is not None and resp.feedback.instruction.value == "WRAP_UP"

    over = Session(plan=PLAN, coaching_plan=plan).step(200_000, **gaze_on(0.9))
    assert over.feedback is not None and over.feedback.instruction.value == "WRAP_UP"

    # 시간 마무리도 개입 수에는 센다
    s2 = Session(plan=PLAN, coaching_plan={"max_interventions": 1})
    first = s2.step(170_000)
    assert first.feedback is not None
    later = s2.step(190_000, **gaze_on(0.9))
    assert "BUDGET_EXHAUSTED" in s2.cand(later, "GAZE_ON_SCRIPT").reasons


# ── C. 우선순위 ────────────────────────────────────────────────────────────────


class BrokenSummary(FakeJudge):
    """합계를 지표로 바꾸지 못하는(summarize 가 예외를 내는) 가짜 모듈."""

    def summarize(self, tally):
        raise RuntimeError("summarize 가짜 예외")


def _value(state: CoachState, mission: dict[str, Any], judges=None) -> object:
    """합계가 이미 센 상태(새로 더해지는 시간이 없다)에서 미션의 지금 값."""
    state.cursors = {
        n: Cursor(since_ms=10_000) for n in ("gaze", "volume", "timing", "pace", "filler")
    }
    req = CoachRequest.model_validate(make_request(10_000, plan=PLAN))
    run = judges_mod.run(req, state, judges or fake_judges(), CFG)
    tick = measure.build_tick(req, CFG, state, run)
    return priority._mission_value(tick, Mission.model_validate(mission))


def _target(metric: str, **kw: Any) -> dict[str, Any]:
    return {"metric": metric, "operator": "LTE", "value": 1.0, **kw}


def test_mission_value_comes_from_totals_through_summarize():
    take = {"GAZE": {"script_ratio": 0.2}}
    slide = {"GAZE": {"script_ratio": 0.8}}
    state = CoachState(totals=take, slide_totals={"1": slide})
    take_mission = {**_mission("GAZE", None), "target": _target("script_ratio")}
    slide_mission = {**_mission("GAZE", 1), "target": _target("script_ratio")}
    assert _value(state, take_mission) == 0.2  # Take 합계
    assert _value(state, slide_mission) == 0.8  # 그 장 합계
    other = {**_mission("GAZE", 7), "target": _target("script_ratio")}
    assert _value(state, other) is None  # 아직 오지 않은 장


class OwnAreas(FakeJudge):
    """자기 영역의 합계만 지표로 바꾸고, 어느 모듈이 바꿨는지 남기는 가짜 모듈."""

    AREAS = {
        "gaze": ("GAZE",),
        "pace": ("SPEED",),
        "volume": ("VOLUME", "PAUSE"),
        "filler": ("FILLER",),
    }

    def summarize(self, tally):
        return {a: {**tally.get(a, {}), "module": self.name} for a in self.AREAS[self.name]}


def test_mission_value_for_each_area_uses_its_module():
    totals = {
        "GAZE": {"script_ratio": 0.3},
        "SPEED": {"cpm": 310.0},
        "VOLUME": {"voice_diff_db": -4.0},
        "PAUSE": {"long_silence_count": 2},
        "FILLER": {"filler_per_min": 5.5},
    }
    state = CoachState(totals=totals)
    judges = fake_judges(**{n: OwnAreas(n) for n in ("gaze", "pace", "volume", "filler")})
    for area, metric, expected, module in [
        ("GAZE", "script_ratio", 0.3, "gaze"),
        ("SPEED", "cpm", 310.0, "pace"),
        ("VOLUME", "voice_diff_db", -4.0, "volume"),
        ("PAUSE", "long_silence_count", 2, "volume"),
        ("FILLER", "filler_per_min", 5.5, "filler"),
    ]:
        assert (
            _value(state, {**_mission(area, None), "target": _target(metric)}, judges) == expected
        )
        by = {**_mission(area, None), "target": _target("module")}
        assert _value(state, by, judges) == module


def test_time_mission_value_is_the_duration_from_the_timing_summary():
    state = CoachState(
        totals={"TIME": {"elapsed_ms": 9_000, "planned_ms": 4_500}},
        slide_totals={"1": {"TIME": {"elapsed_ms": 7_000}}},
    )
    slide = {**_mission("TIME", 1), "target": _target("slide_duration_ms")}
    assert _value(state, slide) == 7_000  # 그 장의 duration_ms
    take = {**_mission("TIME", None), "target": _target("duration_ms")}
    assert _value(state, take) == 9_000
    # slide_duration_ms 는 장 미션의 이름이다 — Take 미션은 Take 시간으로 바꿔 읽지 않는다
    take_named_slide = {**_mission("TIME", None), "target": _target("slide_duration_ms")}
    assert _value(state, take_named_slide) is None


def test_a_config_without_the_filler_rule_still_decides():
    issues = {k: v for k, v in CFG.issues.items() if k != Issue.FILLER_FREQUENT}
    cfg = CFG.model_copy(update={"issues": issues})
    s = Session(cfg, judges=fake_judges(filler=TalliedFiller(3_000, {3_000: [2_500]})))
    for t in range(1_000, 6_000, 1_000):
        resp = s.step(t)
        assert "INTERNAL_ERROR" not in resp.reason_codes
    assert CoachState.model_validate(s.state).filler_times == []


def test_mission_value_is_missing_when_summarize_raises():
    state = CoachState(totals={"GAZE": {"script_ratio": 0.9}})
    judges = fake_judges(gaze=BrokenSummary("gaze"))
    mission = {**_mission("GAZE", None), "target": _target("script_ratio")}
    assert _value(state, mission, judges) is None


class BrokenGaze(ScriptedJudge):
    """대본 응시 문제는 내지만 합계를 지표로 바꾸지 못하는 시선 모듈."""

    def summarize(self, tally):
        raise RuntimeError("summarize 가짜 예외")


def _risk(totals: dict[str, Any], *, broken: bool = False) -> list[str]:
    mission = [{**_mission("GAZE", None), "target": _target("script_ratio", value=0.3)}]
    judges = fake_judges(gaze=BrokenGaze("gaze", lambda t: gaze_on(0.75))) if broken else None
    s = Session(missions=mission, judges=judges)
    s.state = dump_state(CoachState(totals=totals))
    return s.run(10_000, 13_000, **({} if broken else gaze_on(0.75)))[-1].reason_codes


def test_mission_at_risk_follows_the_summarized_value():
    assert "MISSION_AT_RISK" in _risk({"GAZE": {"script_ratio": 0.8}})
    assert "MISSION_AT_RISK" not in _risk({"GAZE": {"script_ratio": 0.2}})
    # 지금 값을 못 구하면 위험 가중만 빠진다 — 미션과 관련은 있다
    codes = _risk({"GAZE": {"script_ratio": 0.8}}, broken=True)
    assert "MISSION_RELEVANT" in codes and "MISSION_AT_RISK" not in codes


def _candidate(issue: Issue) -> Candidate:
    return Candidate(
        candidate_id="c",
        issue_type=issue,
        area=ISSUE_TYPE[issue],
        instruction=Instruction.CONTINUE,
        variant="default",
        step=0,
        strategy_key=issue.value,
        episode_key=issue.value,
        severity=0.5,
        confidence=1.0,
        persistence_ms=0,
        slide_number=1,
        evidence={},
        params={},
        metric=measure.ISSUE_METRIC[issue],
    )


@pytest.mark.parametrize(
    ("issue", "metric", "past", "now", "worse"),
    [
        (Issue.GAZE_ON_SCRIPT, "script_ratio", 0.5, 0.65, True),
        (Issue.GAZE_ON_SCRIPT, "script_ratio", 0.5, 0.6, False),
        (Issue.PACE_FAST, "cpm", 380.0, 410.0, True),
        (Issue.PACE_FAST, "cpm", 380.0, 400.0, False),
        (Issue.VOLUME_LOW, "voice_diff_db", -7.0, -10.0, True),
        (Issue.VOLUME_LOW, "voice_diff_db", -7.0, -9.0, False),
        (Issue.BEHIND_SCHEDULE, "required_ratio", 1.1, 1.25, True),
        (Issue.BEHIND_SCHEDULE, "required_ratio", 1.1, 1.15, False),
        (Issue.FILLER_FREQUENT, "recent_filler_count", 6, 9, True),
        (Issue.FILLER_FREQUENT, "recent_filler_count", 6, 8, False),
    ],
)
def test_worsening_per_metric(issue: Issue, metric: str, past, now, worse: bool):
    state = CoachState(history=[HistorySample(t_ms=30_000, **{metric: past})])
    tick = _tick(60_000, metrics={metric: now}, state=state)
    c = _candidate(issue)
    assert c.metric == metric
    assert priority._worsening(tick, c) == (CFG.policy.worsening_weight if worse else 1.0)


def test_only_the_listed_issues_have_a_worsening_metric():
    with_metric = {i for i, m in measure.ISSUE_METRIC.items() if m is not None}
    assert with_metric == {
        Issue.GAZE_ON_SCRIPT,
        Issue.PACE_FAST,
        Issue.VOLUME_LOW,
        Issue.BEHIND_SCHEDULE,
        Issue.FILLER_FREQUENT,
    }
    assert set(measure.ISSUE_METRIC) == set(Issue) - {Issue.IMPROVED_AFTER_FEEDBACK}


def test_history_keeps_the_short_averages_and_filler_count():
    s = Session()
    s.step(10_000, metrics={"script_ratio_short": 0.5, "recent_filler_count": 4})
    sample = CoachState.model_validate(s.state).history[-1]
    assert (sample.script_ratio_short, sample.recent_filler_count) == (0.5, 4)
