"""finalize — 마지막 창 · 닫기 · 센 구간(ReplayRequired) · Take 결과의 areas · criteria."""

from __future__ import annotations

from typing import Any

import pytest

import pitch_coach_ai.features.coach.core as core_mod
from pitch_coach_ai.features.coach import ReplayRequired, finalize
from pitch_coach_ai.features.coach.config import load_config
from pitch_coach_ai.features.coach.judges import Judges
from pitch_coach_ai.features.coach.state import CoachState, dump_state, load_state
from pitch_coach_ai.features.coach.timing import criteria as timing_criteria

from .conftest import PLAN, Session, gaze_on
from .fakes import AREAS, FakeJudge, ScriptedJudge, fake_judges


class SummaryJudge(FakeJudge):
    """summarize 가 합계를 그대로 지표로 내고 측정 비율은 합계에 적힌 값(없으면 1.0)을 쓴다.

    군더더기 모듈은 Take 끝(take_end_ms)이 오면 첫 단어를 군더더기 하나로 센다(보류 단어 시험).
    """

    def judge(self, inputs: dict[str, Any], t_ms: int) -> list[dict[str, Any]]:
        results = super().judge(inputs, t_ms)
        if self.name == "filler" and inputs["take_end_ms"] == t_ms and inputs["words"]:
            first = inputs["words"][0]
            results[0]["tally"].append({"t_ms": first["start_ms"], "values": {"filler_count": 1}})
        return results

    def summarize(self, tally: dict[str, dict[str, float]]) -> dict[str, Any]:
        out = {a: {"measured_ratio": 1.0, **v} for a, v in tally.items()}
        if "FILLER" in out:  # 군더더기는 묶음 값(중첩 dict)도 낸다
            count = int(out["FILLER"].get("filler_count", 0))
            out["FILLER"] |= {"by_tier": {"T1": count}, "by_word": {"음": count}}
        return out


class RatioJudge(ScriptedJudge):
    """문제 · 지표를 정해 주는 가짜 모듈에 측정 비율 1.0 을 내는 summarize 를 더한 것."""

    def summarize(self, tally: dict[str, dict[str, float]]) -> dict[str, Any]:
        return {a: {"measured_ratio": 1.0, **v} for a, v in tally.items()}


def judges(**over: FakeJudge) -> Judges:
    return fake_judges(**{n: over.get(n) or SummaryJudge(n) for n in AREAS})


def session(**kw: Any) -> Session:
    return Session(judges=kw.pop("judges", None) or judges(), **kw)


def finish(s: Session, t_ms: int, **extra: Any):
    req = {"take_id": "test-take", "t_ms": t_ms, "coach_state": s.state, **extra}
    return finalize(req, s.judges, s.config)


def from_state(t_ms: int = 60_000, *, judges_: Judges | None = None, **fields: Any):
    """센 구간이 Take 를 덮은 coach_state 를 직접 만들어 마지막 창 없이 finalize 한다."""
    fields.setdefault("covered", [[0, t_ms]])
    # 마지막 처리가 Take 끝보다 뒤라 마지막 창을 부르지 않는다(finalize 가 낡은 t 로 건너뛴다)
    state = CoachState(last_t_ms=t_ms + 1, **fields)
    req = {"take_id": "test-take", "t_ms": t_ms, "plan": PLAN, "coach_state": dump_state(state)}
    return finalize(req, judges_ or judges())


# ── 마지막 창 ────────────────────────────────────────────────────────────


def test_last_window_gets_take_end_as_take_end_ms_not_as_an_utterance_end():
    s = session()
    s.run(1_000, 10_000)
    # evaluate 에서는 take_end_ms 가 없다
    assert s.judges.filler.calls[-1][0]["take_end_ms"] is None
    last = {
        "words": [{"word": "음", "start_ms": 10_200, "end_ms": 10_600}],
        "utterance_ends": [8_000],
    }
    fin = finish(s, 11_000, inputs=last)

    for module in (s.judges.filler, s.judges.pace):
        inputs, t = module.calls[-1]
        # Take 끝은 문장 끝 신호에 끼워 넣지 않고 take_end_ms 로 따로 받는다
        assert t == 11_000 and inputs["take_end_ms"] == 11_000
        assert inputs["utterance_ends"] == [8_000]
    # 보류했던 단어가 Take 끝에 군더더기로 세어진다(테스트용 군더더기 모듈이 take_end_ms 를 본다)
    assert fin.take_result.areas["FILLER"].take["filler_count"] == 1


def test_a_real_utterance_end_at_the_take_end_is_passed_as_it_is():
    # 실제 UtteranceEnd 가 Take 끝과 같은 시각이어도 문장 끝 신호는 받은 그대로이고, Take 끝은
    # take_end_ms 로 따로 간다(둘을 섞지 않으므로 구분된다)
    s = session()
    s.run(1_000, 10_000)
    finish(s, 11_000, inputs={"utterance_ends": [11_000]})
    for module in (s.judges.filler, s.judges.pace):
        inputs, _ = module.calls[-1]
        assert inputs["utterance_ends"] == [11_000] and inputs["take_end_ms"] == 11_000


def test_last_window_keeps_the_stt_status_the_be_sent_even_if_the_audio_stopped():
    s = session()
    s.run(1_000, 10_000)
    stopped = {"voice_records": [{"t_ms": 10_000, "audio_live": False}], "stt_status": "ok"}
    finish(s, 11_000, inputs=stopped)
    assert s.judges.filler.calls[-1][0]["stt_status"] == "ok"
    assert s.judges.pace.calls[-1][0]["stt_status"] == "ok"


def test_last_window_is_skipped_only_when_the_take_end_is_before_the_last_tick():
    s = session()
    s.run(1_000, 10_000)
    before = len(s.judges.gaze.calls)
    # 마지막 처리보다 이른 시각은 낡은 요청이라 마지막 창을 부르지 않는다
    fin = finish(s, 9_000)
    assert len(s.judges.gaze.calls) == before
    assert fin.take_result.duration_ms == 9_000
    # 같은 시각이면 부른다(STT 를 닫은 뒤 확정된 단어 · Take 끝 신호가 있을 수 있다)
    finish(s, 10_000)
    assert len(s.judges.gaze.calls) == before + 1


def test_last_window_at_the_same_tick_counts_words_confirmed_after_stt_closed():
    # 마지막 evaluate 가 10 초까지 처리한 뒤 STT 를 닫아 확정된 단어(끝 ≤ 10 초)는 finalize 의
    # 같은 시각 마지막 창에서 Take 합계에 들어간다. 시간 몫은 시각 커서라 다시 세지 않는다
    s = Session(plan=PLAN)
    s.judges = fake_judges(
        **{n: RatioJudge(n, s._spec(n)) for n in ("gaze", "pace", "volume", "filler")}
    )
    s.run(1_000, 10_000, **gaze_on(0.9))
    open_state = load_state(s.state)[0]
    assert open_state.episodes  # 문제 구간이 열려 있다

    late = {
        "words": [
            {"word": "가가가", "start_ms": 9_000, "end_ms": 9_500},
            {"word": "나나나나", "start_ms": 9_500, "end_ms": 10_000},
        ]
    }
    base = finish(s, 10_000)
    fin = finish(s, 10_000, inputs=late)

    # STT 모듈과 판정 모듈이 take_end_ms 를 받는다
    assert s.judges.pace.calls[-1][1] == 10_000
    assert s.judges.pace.calls[-1][0]["take_end_ms"] == 10_000
    # 뒤늦게 확정된 단어는 합계에 들어간다
    assert fin.take_result.areas["SPEED"].take["chars"] == 7
    assert "chars" not in base.take_result.areas["SPEED"].take
    # 시간은 두 번 세지 않는다
    assert fin.take_result.duration_ms == base.take_result.duration_ms == 10_000
    assert fin.take_result.areas["TIME"].take["duration_ms"] == 10_000
    for area in ("GAZE", "SPEED", "VOLUME", "FILLER", "TIME"):
        assert fin.take_result.areas[area].take.get("total_ms") == base.take_result.areas[
            area
        ].take.get("total_ms")
    assert fin.take_result.areas["GAZE"].take["total_ms"] == 10_000
    # 열린 문제 구간의 믿을 수 있던 시간도 늘지 않는다
    episodes = [(e.issue_type, e.reliable_ms) for e in fin.events if e.kind == "EPISODE"]
    assert episodes == [(e.issue_type, e.reliable_ms) for e in base.events if e.kind == "EPISODE"]
    assert any(reliable > 0 for _, reliable in episodes)


def test_last_window_failure_is_counted_as_unmeasured_time(monkeypatch: pytest.MonkeyPatch):
    s = session()
    s.run(1_000, 10_000)

    def boom(*a: Any, **k: Any):
        raise RuntimeError("가짜 예외")

    monkeypatch.setattr(core_mod, "_round", boom)
    fin = finish(s, 11_000)
    # 마지막 1초는 잴 수 없던 시간으로 합계에만 센다
    assert fin.take_result.areas["GAZE"].take["total_ms"] == 11_000


def test_last_window_failure_still_closes_the_slide_it_switched_to(
    monkeypatch: pytest.MonkeyPatch,
):
    s = session()
    s.run(1_000, 10_000)  # 1번 장

    def boom(*a: Any, **k: Any):
        raise RuntimeError("가짜 예외")

    monkeypatch.setattr(core_mod, "_round", boom)
    fin = finish(s, 11_000, inputs={"slide": {"number": 2, "started_ms": 10_500}})
    closed = [(e.slide_number, e.start_ms, e.end_ms) for e in fin.events if e.kind == "SLIDE"]
    assert closed == [(1, 0, 10_500), (2, 10_500, 11_000)]  # 방문은 장이 시작된 시각부터
    assert [x.slide_number for x in fin.take_result.areas["GAZE"].slides] == [1, 2]


# ── 센 구간 ──────────────────────────────────────────────────────────────


def test_replay_required_without_coach_state():
    with pytest.raises(ReplayRequired) as e:
        finalize({"take_id": "test-take", "t_ms": 30_000, "coach_state": None}, judges())
    assert e.value.missing == [(0, 30_000)]
    assert "[0, 30000)" in str(e.value)


def test_replay_required_when_state_was_reset():
    old_version = {"v": -1, "last_t_ms": 29_000}
    with pytest.raises(ReplayRequired) as e:
        finalize({"take_id": "test-take", "t_ms": 30_000, "coach_state": old_version}, judges())
    assert e.value.missing == [(0, 30_000)]


def test_replay_required_when_a_response_was_missing_longer_than_the_window():
    s = session()
    s.run(1_000, 5_000)
    s.step(50_000)  # 창(30초)보다 오래 빠졌다: [5000, 20000) 는 입력에 없었다
    with pytest.raises(ReplayRequired) as e:
        finish(s, 50_000)
    assert e.value.missing == [(5_000, 20_000)]
    assert "[5000, 20000)" in str(e.value)


def test_replay_required_when_the_take_ends_long_after_the_last_response():
    s = session()
    s.run(1_000, 5_000)
    with pytest.raises(ReplayRequired) as e:
        finish(s, 60_000)  # 마지막 창이 30초라 [5000, 30000) 는 셀 수 없다
    assert e.value.missing == [(5_000, 30_000)]


def test_replay_required_after_a_jump_started_the_state_fresh():
    # 0초부터 센 뒤 60초 넘게 앞선 요청(T_MS_JUMP)으로 새로 시작하면 앞부분 합계가 없어 409
    s = session()
    s.run(1_000, 5_000)
    assert s.step(100_000).reason_codes == ["T_MS_JUMP"]
    s.run(101_000, 103_000)
    with pytest.raises(ReplayRequired) as e:
        finish(s, 103_000)
    # 새로 시작한 첫 요청(101_000)은 창 앞(71_000)부터 센다
    assert e.value.missing == [(0, 71_000)]


def test_no_replay_when_the_gap_fits_in_the_window():
    s = session()
    s.run(1_000, 5_000)
    s.step(20_000)  # 15초 빠졌지만 창 안이라 센다
    fin = finish(s, 20_000)
    assert fin.take_result.areas["GAZE"].take["total_ms"] == 20_000


# ── 닫기 ────────────────────────────────────────────────────────────────


def test_finalize_closes_what_is_open_like_before():
    s = Session(judges=judges())
    s.run(10_000, 15_000, issues=[], metrics={})
    fin = finish(s, 15_000)
    assert [e.kind for e in fin.events] == ["SLIDE"]
    assert fin.events[0].end_ms == 15_000


# ── areas ───────────────────────────────────────────────────────────────


class OwnAreas(SummaryJudge):
    """자기 영역의 합계만 지표로 바꾸고, 어느 모듈이 바꿨는지 남긴다."""

    def summarize(self, tally: dict[str, dict[str, float]]) -> dict[str, Any]:
        mine = {a: v for a, v in super().summarize(tally).items() if a in AREAS[self.name]}
        return {a: {**v, "by": self.name} for a, v in mine.items()}


def test_each_area_is_summarized_by_its_module():
    totals = {a: {"measured_ratio": 1.0} for a in ("GAZE", "SPEED", "VOLUME", "PAUSE", "FILLER")}
    fin = from_state(totals=totals, judges_=judges(**{n: OwnAreas(n) for n in AREAS}))
    by = {a: r.take["by"] for a, r in fin.take_result.areas.items() if a != "TIME"}
    assert by == {
        "GAZE": "gaze",
        "SPEED": "pace",
        "VOLUME": "volume",
        "PAUSE": "volume",
        "FILLER": "filler",
    }


def test_areas_come_from_totals_through_summarize():
    fin = from_state(
        totals={
            "GAZE": {"measured_ratio": 0.9, "script_ratio": 0.3},
            "SPEED": {"measured_ratio": 0.8, "cpm": 310.0},
            "VOLUME": {"measured_ratio": 0.7, "level_db": -30.0},
            "PAUSE": {"measured_ratio": 1.0, "long_silence_count": 2},
            "FILLER": {"measured_ratio": 0.6, "filler_count": 4},
            "TIME": {"elapsed_ms": 60_000, "planned_ms": 54_000},
        },
    )
    r = fin.take_result
    assert list(r.areas) == ["GAZE", "SPEED", "VOLUME", "PAUSE", "FILLER", "TIME"]
    gaze = r.areas["GAZE"]
    assert gaze.measured_ratio == 0.9 and gaze.take == {"script_ratio": 0.3}
    assert gaze.unmeasured_reason is None and gaze.slides == []
    assert r.areas["SPEED"].take == {"cpm": 310.0}
    assert r.areas["PAUSE"].take == {"long_silence_count": 2}
    # 묶음 값은 고치지 않고 그대로 담는다
    assert r.areas["FILLER"].take == {
        "filler_count": 4,
        "by_tier": {"T1": 4},
        "by_word": {"음": 4},
    }
    time = r.areas["TIME"]
    assert time.measured_ratio == 0.9 and time.take == {"duration_ms": 60_000}


def test_slides_merge_revisits_and_carry_span_and_target():
    s = session(plan=PLAN)
    s.run(1_000, 9_000, slide=1, slide_started=0)
    s.run(10_000, 19_000, slide=2, slide_started=10_000)
    s.run(20_000, 29_000, slide=1, slide_started=20_000)
    fin = finish(
        s,
        30_000,
        plan=PLAN,
        inputs={"slide": {"number": 1, "started_ms": 20_000}},
    )
    r = fin.take_result
    gaze = r.areas["GAZE"]
    assert gaze.take == {"total_ms": 30_000}
    one, two = gaze.slides
    assert (one.slide_number, two.slide_number) == (1, 2)
    # 다시 온 장은 한 번호로 합친다
    assert one.metrics == {"total_ms": 20_000} and two.metrics == {"total_ms": 10_000}
    # 시작은 첫 방문, 끝은 마지막 방문의 끝(마지막 장은 Take 끝)
    assert (one.start_ms, one.end_ms, one.target_ms) == (0, 30_000, 30_000)
    assert (two.start_ms, two.end_ms, two.target_ms) == (10_000, 20_000, 60_000)

    time = r.areas["TIME"]
    assert [x.metrics for x in time.slides] == [
        {"slide_duration_ms": 20_000},
        {"slide_duration_ms": 10_000},
    ]
    assert time.take == {"duration_ms": 30_000}


def test_slide_that_is_not_in_the_plan_has_no_target():
    fin = from_state(
        totals={"GAZE": {"measured_ratio": 1.0}},
        slide_totals={"7": {"GAZE": {"measured_ratio": 1.0, "script_ratio": 0.2}}},
        slide_spans={"7": [1_000, 9_000]},
    )
    (slide,) = [x for x in fin.take_result.areas["GAZE"].slides]
    assert slide.target_ms is None and (slide.start_ms, slide.end_ms) == (1_000, 9_000)


def test_area_below_the_measured_ratio_is_left_empty():
    fin = from_state(
        totals={
            "GAZE": {"measured_ratio": 0.49, "script_ratio": 0.3},
            "SPEED": {"measured_ratio": 0.5, "cpm": 300.0},
        },
        slide_totals={
            "1": {"GAZE": {"measured_ratio": 0.9}, "SPEED": {"measured_ratio": 0.9, "cpm": 1.0}}
        },
    )
    gaze = fin.take_result.areas["GAZE"]
    assert gaze.measured_ratio == 0.49
    assert gaze.take is None and gaze.slides is None
    assert gaze.unmeasured_reason == "LOW_MEASURED_RATIO"
    # 기준과 같으면 기준 미만이 아니다
    speed = fin.take_result.areas["SPEED"]
    assert speed.take == {"cpm": 300.0} and speed.unmeasured_reason is None
    # 합계를 읽을 수 없는(측정 비율이 없는) 영역도 비운다
    pause = fin.take_result.areas["PAUSE"]
    assert pause.measured_ratio is None and pause.unmeasured_reason == "LOW_MEASURED_RATIO"


def test_threshold_comes_from_the_config():
    state = CoachState(
        last_t_ms=1_001, covered=[[0, 1_000]], totals={"GAZE": {"measured_ratio": 0.6}}
    )
    req = {"take_id": "test-take", "t_ms": 1_000, "coach_state": dump_state(state)}
    strict = load_config(take_result={"min_measured_ratio": 0.8})
    assert finalize(req, judges(), strict).take_result.areas["GAZE"].take is None
    assert finalize(req, judges()).take_result.areas["GAZE"].take == {}


def test_slide_below_the_measured_ratio_only_loses_its_metrics():
    fin = from_state(
        totals={"GAZE": {"measured_ratio": 0.8}},
        slide_totals={
            "1": {"GAZE": {"measured_ratio": 0.9, "script_ratio": 0.2}},
            "2": {"GAZE": {"measured_ratio": 0.3, "script_ratio": 0.9}},
        },
    )
    gaze = fin.take_result.areas["GAZE"]
    assert gaze.take == {} and gaze.unmeasured_reason is None
    one, two = gaze.slides
    assert one.metrics == {"script_ratio": 0.2} and one.measured_ratio == 0.9
    assert two.metrics is None and two.measured_ratio == 0.3


@pytest.mark.parametrize("source", ["CALIBRATION", "TAKE", None])
def test_volume_take_carries_the_base_level_source(source: str | None):
    fin = from_state(
        totals={"VOLUME": {"measured_ratio": 1.0, "level_db": -30.0}},
        base_level_source=source,
    )
    volume = fin.take_result.areas["VOLUME"]
    assert volume.take == {"level_db": -30.0, "base_level_source": source}


# ── criteria ────────────────────────────────────────────────────────────


def test_criteria_split_volume_module_into_volume_and_pause():
    fin = from_state(
        latest_criteria_versions={"volume": "volume-9", "pace": "pace-3", "timing": "timing-1"},
        criteria_versions={"volume": "volume-9", "pace": "pace-3", "timing": "timing-1"},
    )
    c = fin.take_result.criteria
    assert list(c) == ["GAZE", "SPEED", "VOLUME", "PAUSE", "FILLER", "TIME"]
    assert set(c["VOLUME"].issues) == {"VOLUME_LOW"}
    assert set(c["PAUSE"].issues) == {"LONG_SILENCE"}
    assert c["VOLUME"].criteria_version == c["PAUSE"].criteria_version == "volume-9"
    assert set(c["SPEED"].issues) == {"PACE_FAST"} and c["SPEED"].criteria_version == "pace-3"
    assert c["VOLUME"].issues["VOLUME_LOW"].direction == "LOWER_IS_WORSE"
    assert set(c["TIME"].issues) == {k.value for k in timing_criteria()}
    assert c["GAZE"].issues == {} and c["GAZE"].criteria_version is None
    assert fin.take_result.criteria_changed is False


def test_criteria_come_from_the_round_that_ran_last():
    s = session()
    s.run(1_000, 10_000)
    fin = finish(s, 11_000)
    c = fin.take_result.criteria
    assert c["SPEED"].criteria_version == "pace-fake-1"
    assert c["TIME"].criteria_version == s.responses[-1].meta.criteria_versions["timing"]
    assert fin.meta.criteria_versions == {
        **s.responses[-1].meta.criteria_versions,
        "coach": s.responses[-1].meta.criteria_versions["coach"],
    }
    assert fin.take_result.criteria_changed is False


def test_criteria_changed_when_a_version_changed_during_the_take():
    same = from_state(
        criteria_versions={"gaze": "gaze-1"}, latest_criteria_versions={"gaze": "gaze-1"}
    )
    changed = from_state(
        criteria_versions={"gaze": "gaze-1", "pace": "pace-1"},
        latest_criteria_versions={"gaze": "gaze-1", "pace": "pace-2"},
    )
    assert same.take_result.criteria_changed is False
    assert changed.take_result.criteria_changed is True
    assert changed.take_result.criteria["SPEED"].criteria_version == "pace-2"


def test_criteria_changed_when_a_version_changed_and_came_back():
    # A → B → A: 처음과 마지막이 같아도 Take 도중 바뀐 적이 있으면 true
    back = from_state(
        criteria_versions={"gaze": "gaze-1"},
        latest_criteria_versions={"gaze": "gaze-1"},
        criteria_changed=True,
    )
    assert back.take_result.criteria_changed is True


# ── 시간 계획 ───────────────────────────────────────────────────────────


def test_take_result_carries_the_plan_without_the_slides():
    s = session(plan=PLAN)
    s.run(1_000, 20_000)
    fin = finish(s, 21_000, plan=PLAN)
    assert fin.take_result.plan is not None
    assert fin.take_result.plan.model_dump() == {
        "target_ms": 180_000,
        "min_ms": 165_000,
        "max_ms": 195_000,
    }


def test_take_result_plan_is_null_without_a_plan_in_the_request():
    s = session()
    s.run(1_000, 20_000)
    assert finish(s, 21_000).take_result.plan is None


# ── 응답 ────────────────────────────────────────────────────────────────


def test_response_shape_and_determinism():
    s = session(plan=PLAN)
    s.run(1_000, 20_000)
    first = finish(s, 21_000, plan=PLAN, script_mode="HIGHLIGHT")
    again = finish(s, 21_000, plan=PLAN, script_mode="HIGHLIGHT")
    assert first.model_dump(mode="json") == again.model_dump(mode="json")

    out = first.model_dump(mode="json")
    assert set(out) == {"take_result", "events", "meta"}
    assert set(out["take_result"]) == {
        "take_id",
        "duration_ms",
        "script_mode",
        "plan",
        "replayed",
        "criteria_changed",
        "areas",
        "problem_segments",
        "interventions",
        "gave_up",
        "criteria",
    }
    assert set(out["meta"]) == {"schema_version", "feature_version", "criteria_versions", "model"}
    result = first.take_result
    assert (result.take_id, result.duration_ms) == ("test-take", 21_000)
    assert result.script_mode == "HIGHLIGHT" and result.replayed is False
    assert result.interventions == result.gave_up == []
    assert set(out["take_result"]["areas"]["GAZE"]) == {
        "measured_ratio",
        "take",
        "slides",
        "unmeasured_reason",
    }


def test_request_is_validated():
    with pytest.raises(ValueError):
        finalize({"t_ms": -1, "take_id": "x", "coach_state": {}}, judges())
